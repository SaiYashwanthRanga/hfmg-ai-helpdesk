"""Names, spelled letters and emails: which transcription setup gets them right?

The general voice corpus showed the transcription model assembling spelled
letters into words and autocorrecting them ("m, l, o, p, e, z" -> "MLOpec").
This benchmark isolates that problem with the names HFMG callers actually
have, spelled and spoken the way people do on the phone, in several English
accents, and scores each STT configuration + our letter decoder by exact
match.

    cd backend && python -m eval.spelling_bench synth
    cd backend && python -m eval.spelling_bench run
"""

import argparse
import asyncio
import statistics
import time

from eval.voice_eval import CACHE, SAMPLE_RATE, _add_noise, _wav_bytes  # noqa: F401

# (id, kind, text spoken, expected value)
ITEMS = [
    ("name_yashwanth_spelled", "name_spelled", "Y, A, S, H, W, A, N, T, H.", "yashwanth"),
    ("name_saiyashwanth_spelled", "name_spelled", "S, A, I, Y, A, S, H, W, A, N, T, H.", "saiyashwanth"),
    ("name_ranga_spelled", "name_spelled", "R, A, N, G, A.", "ranga"),
    ("name_nguyen_spelled", "name_spelled", "N, G, U, Y, E, N.", "nguyen"),
    ("name_haddad_spelled", "name_spelled", "H, A, D, D, A, D.", "haddad"),
    ("name_lopez_spelled", "name_spelled", "L, O, P, E, Z.", "lopez"),
    ("name_chandu_spelled", "name_spelled", "C, H, A, N, D, U.", "chandu"),
    ("name_kumar_spelled", "name_spelled", "K, U, M, A, R.", "kumar"),
    ("name_as_in", "name_spelled", "It's Y as in yellow, A, S, H, W as in water, A, N, T as in Tom, H.", "yashwanth"),
    ("email_local_spelled", "email_local", "R, A, N, G, A, dot, S, A, I, Y, A, S, H, W, A, N, T, H.", "ranga.saiyashwanth"),
    ("email_local_spelled_2", "email_local", "P, S, H, A, H.", "pshah"),
    ("email_local_spelled_3", "email_local", "M, L, O, P, E, Z.", "mlopez"),
    ("email_full_spoken", "email", "ranga dot saiyashwanth at h f m g dot net.", "ranga.saiyashwanth@hfmg.net"),
    ("email_full_spelled", "email", "R, A, N, G, A, dot, S, A, I, at H, F, M, G, dot net.", "ranga.sai@hfmg.net"),
]

VOICES = [
    ("indian", "echo", "A caller with a clear Indian English accent, spelling letters one at a time with a short pause between letters."),
    ("indian_fast", "ash", "A caller with an Indian English accent spelling letters quickly, as people do on the phone."),
    ("american", "coral", "An American office worker spelling letters clearly, one at a time."),
]
NOISY_VOICE = "indian"


def clip(item_id: str, voice: str, noisy: bool = False):
    return CACHE / "spelling" / f"{item_id}.{voice}{'.noisy' if noisy else ''}.wav"


async def synth() -> None:
    from openai import AsyncOpenAI

    from app.core.config import get_settings

    client = AsyncOpenAI(api_key=get_settings().openai_api_key, max_retries=2)
    semaphore = asyncio.Semaphore(4)

    async def one(item, voice_label, voice, instructions):
        path = clip(item[0], voice_label)
        if path.exists():
            return
        async with semaphore:
            response = await client.audio.speech.create(
                model="gpt-4o-mini-tts", voice=voice, input=item[2], instructions=instructions, response_format="pcm"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_wav_bytes(response.content))
        if voice_label == NOISY_VOICE:
            clip(item[0], voice_label, True).write_bytes(_wav_bytes(_add_noise(response.content, 10, hash(item[0]) & 0xFFFF)))

    await asyncio.gather(*(one(item, *v) for item in ITEMS for v in VOICES))
    print("spelling clips ready")


def decode(kind: str, transcript: str) -> str | None:
    from app.voice import nlu

    if kind == "name_spelled":
        name = nlu.decode_spelled(transcript)
        return name.lower() if name else None
    if kind == "email_local":
        return nlu.decode_email_local(transcript)
    return nlu.quick_email(transcript) or nlu.normalize_email(nlu.decode_email_local(transcript) or "")


async def run(configs: list[str]) -> None:
    from openai import AsyncOpenAI

    from app.core.config import get_settings
    from app.speech import context

    client = AsyncOpenAI(api_key=get_settings().openai_api_key, max_retries=2)
    cases = [(item, v[0], False) for item in ITEMS for v in VOICES] + [(item, NOISY_VOICE, True) for item in ITEMS]
    state_for = {"name_spelled": "SPELL_NAME", "email_local": "SPELL_EMAIL", "email": "COLLECT_EMAIL"}
    semaphore = asyncio.Semaphore(4)

    for config in configs:
        model, _, prompt_mode = config.partition("+")
        rows = []

        async def one(item, voice_label, noisy):
            data = clip(item[0], voice_label, noisy).read_bytes()
            kwargs = {}
            if prompt_mode == "prompt":
                kwargs["prompt"] = context.transcription_prompt(state_for[item[1]])
            async with semaphore:
                t0 = time.perf_counter()
                result = await client.audio.transcriptions.create(
                    model=model, file=("u.wav", data, "audio/wav"), language="en", **kwargs
                )
                ms = (time.perf_counter() - t0) * 1000
            got = decode(item[1], result.text)
            rows.append((item, voice_label, noisy, result.text, got, got == item[3], ms))

        await asyncio.gather(*(one(*c) for c in cases))
        ok = sum(r[5] for r in rows)
        print(f"\n{config:<34} exact {ok}/{len(rows)} ({ok / len(rows):.0%})  avg {statistics.mean(r[6] for r in rows):.0f} ms")
        for kind in ("name_spelled", "email_local", "email"):
            sub = [r for r in rows if r[0][1] == kind]
            print(f"    {kind:<13} {sum(r[5] for r in sub)}/{len(sub)}")
        for r in rows:
            if not r[5]:
                print(f"    miss {r[0][0]}/{r[1]}{'*' if r[2] else ''}: heard {r[3]!r} -> {r[4]!r} (want {r[0][3]!r})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["synth", "run"])
    parser.add_argument("--configs", nargs="+", default=[
        "gpt-4o-mini-transcribe", "gpt-4o-mini-transcribe+prompt", "gpt-4o-transcribe+prompt", "whisper-1+prompt",
    ])
    args = parser.parse_args()
    asyncio.run(synth() if args.command == "synth" else run(args.configs))


if __name__ == "__main__":
    main()
