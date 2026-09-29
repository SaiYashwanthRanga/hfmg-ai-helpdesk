"""Which transcription prompt gets spoken email addresses right?

Runs every COLLECT_EMAIL clip (clean and noisy) through the STT model with
each candidate prompt, then through the same normalization the agent uses,
and counts exact matches against the corpus truth.

    cd backend && python -m eval.email_prompt_bench
"""

import asyncio
import statistics
import time

from eval.corpus import NOISY, SCENARIOS
from eval.voice_eval import clip_path

VOCAB = "HFMG IT help desk phone call. Vocabulary: HFMG, hfmg.net."
PROMPTS = {
    "none": None,
    "example (current)": (
        f"{VOCAB} The caller says or spells an email address, usually at hfmg.net, "
        "for example: m lopez at h f m g dot net, which is mlopez@hfmg.net."
    ),
    "format rules, no example": (
        f"{VOCAB} The caller says or spells an email address. Keep every part they say, "
        "including first names before 'dot'. Single letters are spelled out. "
        "'at' means @ and 'dot' means a period. Most addresses end in @hfmg.net."
    ),
    "literal words": (
        f"{VOCAB} The caller is reading out an email address. Write exactly the words and "
        "single letters they say, for example: suresh dot kumar at h f m g dot net."
    ),
}


async def main() -> None:
    from openai import AsyncOpenAI

    from app.core.config import get_settings
    from app.voice import nlu

    settings = get_settings()
    client = AsyncOpenAI(api_key=settings.openai_api_key, max_retries=2)
    clips = [
        (s, noisy)
        for s in SCENARIOS
        if s["truth"].get("email")
        for noisy in ([False, True] if s["id"] in NOISY else [False])
    ]
    for label, prompt in PROMPTS.items():
        correct, latencies, misses = 0, [], []
        for scenario, noisy in clips:
            data = clip_path(scenario["id"], "COLLECT_EMAIL", noisy).read_bytes()
            kwargs = {"prompt": prompt} if prompt else {}
            t0 = time.perf_counter()
            result = await client.audio.transcriptions.create(
                model=settings.speech_stt_model, file=("u.wav", data, "audio/wav"), language="en", **kwargs
            )
            latencies.append((time.perf_counter() - t0) * 1000)
            email = nlu.quick_email(result.text)
            if email == scenario["truth"]["email"]:
                correct += 1
            else:
                misses.append(f"{scenario['id']}{'*' if noisy else ''}: {result.text!r} -> {email}")
        print(f"\n{label:<26} exact {correct}/{len(clips)}  avg {statistics.mean(latencies):.0f} ms")
        for miss in misses:
            print(f"    miss {miss}")


if __name__ == "__main__":
    asyncio.run(main())
