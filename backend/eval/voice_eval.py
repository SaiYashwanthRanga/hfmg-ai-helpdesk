"""Voice agent evaluation harness: latency and understanding, end to end.

Real audio through the real pipeline: caller utterances from corpus.py are
synthesized once with OpenAI TTS (distinct voices, accents, pace, optional
background noise), then played into the AI Call Simulator's API --
POST /audio (speech-to-text) and POST /process (the production orchestrator
and NLU, then text-to-speech) -- exactly as the browser does. Every turn's
stage timings and every extracted field are recorded and scored.

Runs in-process against its own throwaway database (<dev db name> is never
touched; the harness uses `hfmg_voice_eval`). Uses the OPENAI_API_KEY from
backend/.env and costs real (small) money: a full `e2e` run is ~100 STT +
~100 NLU + ~110 TTS calls.

    cd backend
    python -m eval.voice_eval synth                  # once; cached in eval/.cache
    python -m eval.voice_eval e2e --label before     # results in eval/results/before.json
    python -m eval.voice_eval report before after    # side-by-side comparison
    python -m eval.voice_eval stt-bench | nlu-bench | tts-bench
"""

import argparse
import array
import asyncio
import json
import math
import os
import random
import re
import statistics
import sys
import time
import uuid
import wave
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
BACKEND = EVAL_DIR.parent
CACHE = EVAL_DIR / ".cache"
RESULTS = EVAL_DIR / "results"
EVAL_DB = "hfmg_voice_eval"
SAMPLE_RATE = 24_000  # OpenAI TTS "pcm" output: 24 kHz, 16-bit, mono

sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # pydantic-settings reads ".env" relative to the working directory


def _configure_environment() -> None:
    """Point the app at the eval database before anything imports it."""
    from dotenv import dotenv_values
    from sqlalchemy.engine import make_url

    base = os.environ.get("DATABASE_URL") or dotenv_values(BACKEND / ".env").get("DATABASE_URL")
    url = make_url(base).set(database=EVAL_DB)
    os.environ.update(
        DATABASE_URL=url.render_as_string(hide_password=False),
        ENABLE_VOICE_SIMULATOR="true",
        ENVIRONMENT="development",
        ENABLE_AI_SUMMARY="false",
        ENABLE_EMAIL_NOTIFICATIONS="false",
        SIMULATOR_MAX_SESSIONS_PER_HOUR="100000",
    )


_configure_environment()

from eval.corpus import NOISY, SCENARIOS  # noqa: E402

# --- audio -------------------------------------------------------------------


def _wav_bytes(pcm: bytes) -> bytes:
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buffer.getvalue()


def _add_noise(pcm: bytes, snr_db: float, seed: int) -> bytes:
    """Office-like noise: white noise plus a 60 Hz hum and random 'chatter' bursts."""
    rng = random.Random(seed)
    samples = array.array("h", pcm)
    signal_rms = math.sqrt(sum(s * s for s in samples) / max(1, len(samples))) or 1.0
    noise_rms = signal_rms / (10 ** (snr_db / 20))
    out = array.array("h")
    burst = 0.0
    for i, s in enumerate(samples):
        if i % 2400 == 0:  # every 100 ms, maybe start or stop a chatter burst
            burst = rng.uniform(0.5, 1.6) if rng.random() < 0.35 else 0.4
        hum = 0.35 * math.sin(2 * math.pi * 60 * i / SAMPLE_RATE)
        n = (rng.gauss(0, 1) * burst + hum) * noise_rms
        out.append(max(-32768, min(32767, int(s + n))))
    return out.tobytes()


def clip_path(scenario_id: str, state: str, noisy: bool) -> Path:
    return CACHE / scenario_id / f"{state}{'.noisy' if noisy else ''}.wav"


async def synth() -> None:
    from openai import AsyncOpenAI

    from app.core.config import get_settings

    client = AsyncOpenAI(api_key=get_settings().openai_api_key, max_retries=2)
    semaphore = asyncio.Semaphore(4)
    made = 0

    async def one(scenario: dict, state: str, text: str) -> None:
        nonlocal made
        clean = clip_path(scenario["id"], state, False)
        noisy = clip_path(scenario["id"], state, True)
        if clean.exists() and (scenario["id"] not in NOISY or noisy.exists()):
            return
        async with semaphore:
            response = await client.audio.speech.create(
                model="gpt-4o-mini-tts",
                voice=scenario["voice"],
                input=text,
                instructions=scenario["instructions"],
                response_format="pcm",
            )
        pcm = response.content
        clean.parent.mkdir(parents=True, exist_ok=True)
        clean.write_bytes(_wav_bytes(pcm))
        if scenario["id"] in NOISY:
            noisy.write_bytes(_wav_bytes(_add_noise(pcm, snr_db=10, seed=hash((scenario["id"], state)) & 0xFFFF)))
        made += 1

    await asyncio.gather(*(one(s, state, text) for s in SCENARIOS for state, text in s["answers"].items()))
    print(f"synthesized {made} clip(s) into {CACHE}")


# --- scoring -----------------------------------------------------------------

_NUMBER_WORDS = {w: str(i) for i, w in enumerate("zero one two three four five six seven eight nine".split())}
_NUMBER_WORDS["oh"] = "0"


def normalize_words(text: str) -> list[str]:
    """Tokens for WER that don't penalize equivalent spoken/written forms.

    "eight four five" and "845" both become "845"; "h f m g" becomes "hfmg";
    "@" and "." inside addresses become the words people say.
    """
    text = (text or "").lower().replace("@", " at ")
    text = re.sub(r"(?<=\w)\.(?=\w)", " dot ", text)
    tokens = re.sub(r"[^\w' ]+", " ", text).replace("'", "").split()
    tokens = [_NUMBER_WORDS.get(t, t) for t in tokens]
    merged: list[str] = []
    in_letter_run = False
    for t in tokens:
        if t.isdigit() and merged and merged[-1].isdigit():
            merged[-1] += t
            in_letter_run = False
        elif len(t) == 1 and t.isalpha() and in_letter_run:
            merged[-1] += t
        else:
            merged.append(t)
            in_letter_run = len(t) == 1 and t.isalpha()
    return merged


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize_words(reference), normalize_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    d = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, d[0] = d[0], i
        for j, h in enumerate(hyp, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (r != h))
    return d[len(hyp)] / len(ref)


def _digits(value) -> str:
    return "".join(c for c in str(value or "") if c.isdigit())[-10:]


def score_call(truth: dict, collected: dict, ticket: dict | None, escalated: bool) -> dict:
    if truth.get("escalated"):
        return {"escalated": escalated == truth["escalated"]}
    name = (collected.get("caller_name") or "").lower()
    department = (collected.get("department") or "").lower()
    started = (collected.get("started") or "").lower()
    description = (collected.get("description") or "").lower()
    priority = (ticket or {}).get("priority") or collected.get("priority")
    expected_priority = truth["priority"] if isinstance(truth["priority"], list) else [truth["priority"]]
    return {
        "caller_name": all(part.lower() in name for part in truth["caller_name"].split()),
        "department": any(k in department for k in truth["department"]),
        "phone": _digits(collected.get("phone_number")) == truth["phone"],
        "email": (collected.get("email") or None) == truth["email"],
        "issue": any(k in description for k in truth["issue"]),
        "category": ((ticket or {}).get("category") or collected.get("category")) == truth["category"],
        "priority": priority in expected_priority,
        "started": any(k in started for k in truth["started"]),
        "work_blocked": collected.get("work_blocked") is truth["work_blocked"],
        "ticket_created": ticket is not None,
        "no_escalation": not escalated,
    }


# --- end to end ----------------------------------------------------------------


async def _prepare_database() -> None:
    import asyncpg
    from sqlalchemy.engine import make_url

    from app.core.config import get_settings
    from app.db.base import Base, async_session_factory, engine
    from app.db.models import Category
    from seed import DEFAULT_CATEGORIES

    url = make_url(get_settings().database_url)
    admin = url.set(drivername="postgresql", database="postgres").render_as_string(hide_password=False)
    conn = await asyncpg.connect(admin)
    try:
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", EVAL_DB):
            await conn.execute(f'CREATE DATABASE "{EVAL_DB}"')
    finally:
        await conn.close()
    async with engine.begin() as c:
        await c.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS citext")
        await c.run_sync(Base.metadata.drop_all)
        await c.run_sync(Base.metadata.create_all)
    async with async_session_factory() as db:
        for name, description, priority in DEFAULT_CATEGORIES:
            db.add(Category(name=name, description=description, default_priority=priority))
        await db.commit()


async def _stream_speech(client, speech_path: str) -> tuple[float | None, float | None]:
    """(first byte ms, total ms) for a streamed reply, as the browser receives it."""
    t0 = time.perf_counter()
    first = None
    async with client.stream("GET", f"/api/v1{speech_path}") as response:
        if response.status_code != 200:
            await response.aread()
            return None, None
        async for _chunk in response.aiter_bytes():
            if first is None:
                first = (time.perf_counter() - t0) * 1000
    return (round(first, 1) if first is not None else None), round((time.perf_counter() - t0) * 1000, 1)


def _answer_key(scenario: dict, state: str, turns: list[dict], realistic: bool) -> str:
    """Which recorded answer the caller gives to the question being asked.

    Scripted mode always gives the same answer per question. Realistic mode
    behaves like a person on the phone: it says no when its email is read
    back wrong, and spells the address when asked again.
    """
    answers = scenario["answers"]
    last_collected = next((t["collected_after"] for t in reversed(turns) if t.get("collected_after")), {}) or {}
    # The caller does what the agent asks: spell the email, confirm or
    # correct the spelled name. (Earlier agents asked neither.)
    if state == "CONFIRM_NAME" and "CONFIRM_NAME" in answers:
        # Respond to what the agent just asked, like a caller would.
        last_reply = next((t.get("reply") or "" for t in reversed(turns) if "reply" in t), "")
        if "spell your first name" in last_reply:
            return "SPELL_FIRST"
        if "your last name" in last_reply:
            return "SPELL_LAST" if "SPELL_LAST" in answers else "SPELL_FIRST"
        heard = (last_collected.get("caller_name") or "").lower()
        return "CONFIRM_NAME" if heard == scenario["truth"]["caller_name"].lower() else "CONFIRM_NAME_NO"
    if state == "COLLECT_EMAIL" and "COLLECT_EMAIL_SPELLED" in answers and not realistic:
        return "COLLECT_EMAIL_SPELLED"
    if not realistic:
        return state
    if state == "CONFIRM_EMAIL" and "CONFIRM_EMAIL_NO" in answers:
        heard = next((t["collected_after"].get("email") for t in reversed(turns) if t.get("collected_after")), None)
        return "CONFIRM_EMAIL" if heard == scenario["truth"].get("email") else "CONFIRM_EMAIL_NO"
    if state == "COLLECT_EMAIL" and "COLLECT_EMAIL_SPELLED" in answers:
        return "COLLECT_EMAIL_SPELLED"
    return state


async def run_call(client, scenario: dict, noisy: bool, tts: bool, realistic: bool = False) -> dict:
    base = "/api/v1/voice-simulator"
    started = time.perf_counter()
    response = await client.post(f"{base}/start", json={"tts": tts, "label": f"eval {scenario['id']}{' noisy' if noisy else ''}"})
    response.raise_for_status()
    start = response.json()
    sid, state = start["session"]["id"], start["session"]["state"]
    greeting_tts = start["turn"]["timings"]["tts_ms"]
    if start["greeting"].get("speech_path"):
        greeting_tts, _ = await _stream_speech(client, start["greeting"]["speech_path"])
    turns = [{"state_before": "GREETING", "tts_ms": greeting_tts, "greeting": True}]
    ended = False
    for _ in range(16):
        key = _answer_key(scenario, state, turns, realistic)
        text = scenario["answers"].get(key)
        if text is None:
            turns.append({"state_before": state, "unanswerable": True})
            break
        clip = clip_path(scenario["id"], key, noisy)
        turn_id = str(uuid.uuid4())
        t0 = time.perf_counter()
        audio = await client.post(
            f"{base}/audio",
            data={"session_id": sid, "turn_client_id": turn_id},
            files={"audio": (clip.name, clip.read_bytes(), "audio/wav")},
        )
        t1 = time.perf_counter()
        audio.raise_for_status()
        processed = await client.post(f"{base}/process", json={"session_id": sid, "turn_client_id": turn_id, "input_mode": "voice"})
        t2 = time.perf_counter()
        processed.raise_for_status()
        body = processed.json()
        # Streamed speech (after the latency work): the caller hears the
        # reply at the first audio byte, not when synthesis finishes.
        speech_first_ms = speech_total_ms = None
        t_audio_start = t2
        if body["reply"].get("speech_path"):
            speech_first_ms, speech_total_ms = await _stream_speech(client, body["reply"]["speech_path"])
            t_audio_start = t2 + speech_first_ms / 1000
        turn = body["turn"]
        timings = turn["timings"]
        turns.append({
            "state_before": state,
            "state_after": body["session"]["state"],
            "spoken": text,
            "transcript": audio.json()["transcript"],
            "wer": round(wer(text, audio.json()["transcript"]), 3),
            "intent": turn["intent"],
            "reply": body["reply"]["text"],
            "stt_ms": timings["stt_ms"],
            "llm_ms": timings["llm_ms"],
            # Before: full synthesis inside /process. After: time to first audio byte.
            "tts_ms": speech_first_ms if speech_first_ms is not None else timings["tts_ms"],
            "tts_total_ms": speech_total_ms,
            "queue_wait_ms": timings["queue_wait_ms"],
            "ticket_create_ms": timings["ticket_create_ms"],
            "server_total_ms": timings["server_total_ms"],
            "llm_calls": sum(1 for c in turn["llm_trace"] if c["kind"] != "rule"),
            "rule_answers": sum(1 for c in turn["llm_trace"] if c["kind"] == "rule"),
            "llm_call_ms": round(sum(c["duration_ms"] for c in turn["llm_trace"] if c["kind"] != "rule"), 1),
            "prompt_chars": sum(len(c["system"]) + len(c["user"]) for c in turn["llm_trace"] if c["kind"] != "rule"),
            "audio_request_ms": round((t1 - t0) * 1000, 1),
            "process_request_ms": round((t2 - t1) * 1000, 1),
            # What the caller waits in silence after they stop talking,
            # excluding browser capture/playback: STT + agent + time until
            # the first audio of the reply is available.
            "turn_wait_ms": round((t_audio_start - t0) * 1000, 1),
            "errors": turn["errors"],
            "collected_after": turn["collected_after"],
        })
        state = body["session"]["state"]
        if body["reply"]["call_ended"]:
            ended = True
            break
    if not ended:
        await client.post(f"{base}/end", json={"session_id": sid, "reason": "user_hangup"})
    detail = (await client.get(f"{base}/session/{sid}")).json()
    session = detail["session"]
    collected = dict(session["collected"])
    # Fields the ticket carries but the slot model may not (after the ticket, slots are reset for a 2nd issue).
    for t in reversed(turns):
        if t.get("collected_after") and t["collected_after"].get("caller_name"):
            collected = {**t["collected_after"], **{k: v for k, v in collected.items() if v}}
            break
    return {
        "scenario": scenario["id"],
        "style": scenario["style"],
        "noisy": noisy,
        "turns": turns,
        "final_state": session["state"],
        "escalated": session["escalated"],
        "ticket": session["ticket"],
        "collected": collected,
        "score": score_call(scenario["truth"], collected, session["ticket"], session["escalated"]),
        "wall_ms": round((time.perf_counter() - started) * 1000),
    }


async def e2e(label: str, tts: bool, only: list[str] | None, realistic: bool = False) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    await _prepare_database()
    cases = [(s, False) for s in SCENARIOS] + [(s, True) for s in SCENARIOS if s["id"] in NOISY]
    if only:
        cases = [c for c in cases if c[0]["id"] in only]
    results = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://eval", timeout=120) as client:
        for scenario, noisy in cases:
            result = await run_call(client, scenario, noisy, tts, realistic)
            results.append(result)
            passed = sum(result["score"].values())
            print(f"{scenario['id']:<24}{' noisy' if noisy else '      '} turns={len(result['turns']) - 1:<3} "
                  f"score={passed}/{len(result['score'])} state={result['final_state']}", flush=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{label}.json"
    out.write_text(json.dumps({"label": label, "tts": tts, "realistic": realistic, "results": results}, indent=2))
    print(f"\nwrote {out}")
    print_summary(label, results)


# --- reporting ---------------------------------------------------------------


def _stats(values: list[float]) -> dict:
    values = sorted(v for v in values if v is not None)
    if not values:
        return {"n": 0}
    p95 = values[max(0, math.ceil(0.95 * len(values)) - 1)]
    return {"n": len(values), "min": round(values[0]), "avg": round(statistics.mean(values)), "p95": round(p95), "max": round(values[-1])}


LATENCY_FIELDS = [
    ("stt_ms", "STT"),
    ("llm_ms", "Agent turn (NLU + orchestrator + DB)"),
    ("llm_call_ms", "  of which model calls"),
    ("ticket_create_ms", "Ticket creation"),
    ("queue_wait_ms", "Queue wait (DB + lock)"),
    ("tts_ms", "TTS (before: full synth; after: first audio byte)"),
    ("tts_total_ms", "TTS full stream (after only)"),
    ("server_total_ms", "/process server total"),
    ("turn_wait_ms", "Turn wait (both requests)"),
]


def summarize(results: list[dict]) -> dict:
    turns = [t for r in results for t in r["turns"] if not t.get("greeting") and not t.get("unanswerable")]
    greetings = [t for r in results for t in r["turns"] if t.get("greeting")]
    latency = {key: _stats([t.get(key) for t in turns]) for key, _ in LATENCY_FIELDS}
    latency["greeting_tts_ms"] = _stats([t.get("tts_ms") for t in greetings])
    fields: dict[str, list[bool]] = {}
    for r in results:
        for field, ok in r["score"].items():
            fields.setdefault(field, []).append(ok)
    intake = [r for r in results if "caller_name" in r["score"]]
    return {
        "calls": len(results),
        "turns": len(turns),
        "latency": latency,
        "llm_calls_per_turn": round(statistics.mean(t["llm_calls"] for t in turns), 2) if turns else 0,
        "rule_answered_turns": sum(1 for t in turns if t.get("rule_answers")),
        "turns_over_2500ms": sum(1 for t in turns if (t.get("turn_wait_ms") or 0) > 2500),
        "prompt_chars_avg": round(statistics.mean(t["prompt_chars"] for t in turns)) if turns else 0,
        "stt_wer_avg": round(statistics.mean(t["wer"] for t in turns), 3) if turns else 0,
        "stt_wer_noisy": round(statistics.mean(t["wer"] for r in results if r["noisy"] for t in r["turns"] if "wer" in t), 3) if any(r["noisy"] for r in results) else None,
        "field_accuracy": {f: round(sum(v) / len(v), 3) for f, v in fields.items()},
        "fields_correct": sum(sum(r["score"].values()) for r in intake),
        "fields_total": sum(len(r["score"]) for r in intake),
        "calls_fully_correct": sum(all(r["score"].values()) for r in results),
        "avg_turns_per_intake": round(statistics.mean(len(r["turns"]) - 1 for r in intake), 2) if intake else 0,
        "unclear_turns": sum(1 for t in turns if (t.get("intent") or "").startswith("unclear")),
        "wrong_escalations": sum(1 for r in intake if r["escalated"]),
        "completed_intakes": sum(1 for r in intake if r["ticket"] and not r["escalated"]),
    }


def print_summary(label: str, results: list[dict]) -> None:
    s = summarize(results)
    print(f"\n=== {label}: {s['calls']} calls, {s['turns']} caller turns ===")
    for key, name in LATENCY_FIELDS + [("greeting_tts_ms", "Greeting TTS")]:
        st = s["latency"][key]
        if st.get("n"):
            print(f"{name:<40} min {st['min']:>6}  avg {st['avg']:>6}  p95 {st['p95']:>6}  max {st['max']:>6}  (n={st['n']})")
    for key in ("llm_calls_per_turn", "rule_answered_turns", "turns_over_2500ms", "prompt_chars_avg", "stt_wer_avg", "stt_wer_noisy", "fields_correct", "fields_total",
                "calls_fully_correct", "avg_turns_per_intake", "unclear_turns", "wrong_escalations", "completed_intakes"):
        print(f"{key:<40} {s[key]}")
    print("field accuracy:", json.dumps(s["field_accuracy"]))


def report(labels: list[str]) -> None:
    summaries = {label: summarize(json.loads((RESULTS / f"{label}.json").read_text())["results"]) for label in labels}
    print(json.dumps(summaries, indent=2))


# --- component benchmarks ------------------------------------------------------


async def tts_bench(models: list[str], formats: list[str], reps: int) -> None:
    from openai import AsyncOpenAI

    from app.core.config import get_settings
    from app.voice import scripts

    client = AsyncOpenAI(api_key=get_settings().openai_api_key, max_retries=0)
    lines = [
        scripts.NAME_ASK.format(issue="Outlook"),
        scripts.EMAIL_ASK.format(first_name="Maria"),
        scripts.EMAIL_CONFIRM.format(email=scripts.spoken_email("mlopez@hfmg.net")),
        "Let me create that ticket for you. One moment. Your ticket number is S I M, 2 0 2 6, 3 F 9 A 1 2 C 4. "
        "I've sent it to our IT team and they'll follow up with you. Is there anything else I can help you with?",
    ]
    for model in models:
        for fmt in formats:
            first_byte, total = [], []
            for _ in range(reps):
                for line in lines:
                    t0 = time.perf_counter()
                    async with client.audio.speech.with_streaming_response.create(
                        model=model, voice="alloy", input=line, response_format=fmt
                    ) as response:
                        got_first = None
                        async for _chunk in response.iter_bytes(4096):
                            if got_first is None:
                                got_first = time.perf_counter()
                    first_byte.append((got_first - t0) * 1000)
                    total.append((time.perf_counter() - t0) * 1000)
            print(f"TTS {model:<18} {fmt:<5} first byte {_stats(first_byte)}  full {_stats(total)}", flush=True)


def _stt_prompt(state: str) -> str:
    from app.speech import context

    return context.transcription_prompt(state)


async def stt_bench(configs: list[str], concurrency: int) -> None:
    from openai import AsyncOpenAI

    from app.core.config import get_settings

    client = AsyncOpenAI(api_key=get_settings().openai_api_key, max_retries=2)
    clips = [
        (s["id"], state, text, noisy)
        for s in SCENARIOS
        for state, text in s["answers"].items()
        for noisy in ([False, True] if s["id"] in NOISY else [False])
    ]
    semaphore = asyncio.Semaphore(concurrency)
    for config in configs:
        model, _, prompted = config.partition("+")
        rows = []

        async def one(scenario_id, state, text, noisy):
            data = clip_path(scenario_id, state, noisy).read_bytes()
            kwargs = {"prompt": _stt_prompt(state)} if prompted else {}
            async with semaphore:
                t0 = time.perf_counter()
                result = await client.audio.transcriptions.create(
                    model=model, file=("u.wav", data, "audio/wav"), language="en", response_format="json", **kwargs
                )
                ms = (time.perf_counter() - t0) * 1000
            rows.append({"state": state, "noisy": noisy, "ms": ms, "wer": wer(text, result.text), "text": result.text, "spoken": text})

        await asyncio.gather(*(one(*c) for c in clips))
        entity = [r for r in rows if r["state"] in ("COLLECT_EMAIL", "COLLECT_PHONE", "COLLECT_NAME")]
        print(
            f"STT {config:<30} latency {_stats([r['ms'] for r in rows])}  WER avg {statistics.mean(r['wer'] for r in rows):.3f} "
            f"clean {statistics.mean(r['wer'] for r in rows if not r['noisy']):.3f} noisy {statistics.mean(r['wer'] for r in rows if r['noisy']):.3f} "
            f"entity-turn WER {statistics.mean(r['wer'] for r in entity):.3f}",
            flush=True,
        )
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f"stt_{config.replace('+', '_')}.json").write_text(json.dumps(rows, indent=2))


async def nlu_bench(models: list[str], reps: int) -> None:
    """Latency and correctness of each NLU question type, per model.

    Input is the text the caller actually said (so this isolates NLU from
    STT). Answers resolved by a deterministic rule make no model call and
    are reported separately.
    """
    from app.core import trace
    from app.core.config import get_settings
    from app.voice import nlu, scripts

    settings = get_settings()
    cases = []
    for s in SCENARIOS:
        t, a = s["truth"], s["answers"]
        if t.get("escalated"):
            cases.append(("escalation", a["COLLECT_DESCRIPTION"], t))
            continue
        for kind, state in (("description", "COLLECT_DESCRIPTION"), ("details", "COLLECT_DETAILS"), ("name", "COLLECT_NAME"),
                            ("phone", "COLLECT_PHONE"), ("email", "COLLECT_EMAIL"), ("yes_no", "CONFIRM_EMAIL"), ("no", "ANYTHING_ELSE")):
            cases.append((kind, a[state], t))

    async def run(kind, text):
        if kind in ("description", "escalation"):
            return await nlu.interpret_description(text)
        if kind == "details":
            return await nlu.interpret_details(text, asked=scripts.DETAILS_ASK)
        if kind == "name":
            return await nlu.interpret_name(text)
        if kind == "phone":
            return await nlu.interpret_phone(text)
        if kind == "email":
            return await nlu.interpret_email(text)
        return await nlu.interpret_yes_no("Is that correct?", text)

    def correct(kind, r, t) -> bool:
        x = r.extras
        if kind == "escalation":
            return r.escalation_requested
        if kind == "description":
            pr = t["priority"] if isinstance(t["priority"], list) else [t["priority"]]
            return r.category == t["category"] and r.priority.value in pr and any(k in (r.value or "").lower() for k in t["issue"])
        if kind == "details":
            return x.get("work_blocked") is t["work_blocked"] and any(k in (x.get("started") or "").lower() for k in t["started"])
        if kind == "name":
            return all(p.lower() in (r.value or "").lower() for p in t["caller_name"].split()) and any(
                k in (x.get("department") or "").lower() for k in t["department"])
        if kind == "phone":
            return _digits(r.value) == t["phone"]
        if kind == "email":
            return (r.value or None) == t["email"] if t["email"] else bool(x.get("declined"))
        if kind == "yes_no":
            return r.yes_no is True
        return r.yes_no is False

    for model in models:
        settings.voice_nlu_model = model
        by_kind: dict[str, dict] = {}
        for _ in range(reps):
            for kind, text, t in cases:
                with trace.collect() as tr:
                    t0 = time.perf_counter()
                    r = await run(kind, text)
                    ms = (time.perf_counter() - t0) * 1000
                used_model = any(c.kind != "rule" for c in tr.llm_calls)
                row = by_kind.setdefault(kind, {"ok": 0, "n": 0, "model_ms": [], "rule": 0})
                row["n"] += 1
                row["ok"] += correct(kind, r, t)
                if used_model:
                    row["model_ms"].append(ms)
                else:
                    row["rule"] += 1
        total_ok = sum(v["ok"] for v in by_kind.values())
        total_n = sum(v["n"] for v in by_kind.values())
        all_ms = [m for v in by_kind.values() for m in v["model_ms"]]
        print(f"\nNLU {model}: correct {total_ok}/{total_n} ({total_ok / total_n:.0%}); model-call latency {_stats(all_ms)}", flush=True)
        for kind, v in by_kind.items():
            print(f"   {kind:<12} {v['ok']}/{v['n']}  rule-resolved {v['rule']}/{v['n']}  model {_stats(v['model_ms'])}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("synth")
    e = sub.add_parser("e2e")
    e.add_argument("--label", required=True)
    e.add_argument("--no-tts", action="store_true")
    e.add_argument("--only", nargs="*")
    e.add_argument("--realistic", action="store_true", help="caller rejects a wrong email read-back and spells it")
    r = sub.add_parser("report")
    r.add_argument("labels", nargs="+")
    t = sub.add_parser("tts-bench")
    t.add_argument("--models", nargs="+", default=["gpt-4o-mini-tts", "tts-1"])
    t.add_argument("--formats", nargs="+", default=["mp3", "opus", "aac"])
    t.add_argument("--reps", type=int, default=2)
    s = sub.add_parser("stt-bench")
    s.add_argument("--configs", nargs="+", default=["gpt-4o-mini-transcribe", "gpt-4o-mini-transcribe+prompt", "gpt-4o-transcribe+prompt"])
    s.add_argument("--concurrency", type=int, default=4)
    n = sub.add_parser("nlu-bench")
    n.add_argument("--models", nargs="+", default=["gpt-5-nano", "gpt-4.1-nano", "gpt-4.1-mini", "gpt-4o-mini"])
    n.add_argument("--reps", type=int, default=1)
    args = parser.parse_args()

    if args.command == "synth":
        asyncio.run(synth())
    elif args.command == "e2e":
        asyncio.run(e2e(args.label, tts=not args.no_tts, only=args.only, realistic=args.realistic))
    elif args.command == "report":
        report(args.labels)
    elif args.command == "tts-bench":
        asyncio.run(tts_bench(args.models, args.formats, args.reps))
    elif args.command == "stt-bench":
        asyncio.run(stt_bench(args.configs, args.concurrency))
    elif args.command == "nlu-bench":
        asyncio.run(nlu_bench(args.models, args.reps))


if __name__ == "__main__":
    main()
