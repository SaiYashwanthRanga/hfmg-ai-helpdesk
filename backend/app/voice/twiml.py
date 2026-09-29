"""TwiML response builders.

Uses the Twilio SDK rather than hand-built XML so caller-supplied values
(names, emails) are escaped correctly.
"""

import html
import re

from twilio.twiml.voice_response import Gather, VoiceResponse

from app.core.config import get_settings

settings = get_settings()

GATHER_ACTION = "/api/v1/webhooks/twilio/voice/gather"

_SAY_RE = re.compile(r"<Say[^>]*>(.*?)</Say>", re.DOTALL)


def spoken_text(twiml_body: str) -> str:
    """Everything the agent says in a TwiML response, as plain text.

    Used for the transcript and by the AI Call Simulator, which speaks the
    orchestrator's replies without Twilio.
    """
    return html.unescape(" ".join(_SAY_RE.findall(twiml_body)).strip())


def ends_call(twiml_body: str) -> bool:
    """True when the response hangs up after speaking."""
    return "<Hangup" in twiml_body


def _gather(hints: str | None) -> Gather:
    kwargs = {"hints": hints} if hints else {}
    return Gather(
        input="speech",
        action=GATHER_ACTION,
        method="POST",
        speech_timeout="auto",
        speech_model=settings.voice_speech_model,
        language=settings.voice_language,
        barge_in=True,
        timeout=settings.voice_gather_timeout,
        **kwargs,
    )


def ask(prompt: str, hints: str | None = None) -> str:
    """Speak a prompt and wait for the caller's reply.

    `hints` are phrases Twilio's recognizer should expect for this answer
    (app/speech/context.gather_hints) -- e.g. "hfmg dot net" when asking for
    an email.
    """
    response = VoiceResponse()
    gather = _gather(hints)
    gather.say(prompt, voice=settings.voice_tts_voice)
    response.append(gather)
    # Reached only when the caller says nothing at all -- re-enters the state
    # machine so silence counts as a failure rather than dropping the call.
    response.redirect(f"{GATHER_ACTION}?silent=1", method="POST")
    return str(response)


def say_and_hangup(*lines: str) -> str:
    """Speak one or more final lines, then end the call."""
    response = VoiceResponse()
    for line in lines:
        if line:
            response.say(line, voice=settings.voice_tts_voice)
    response.hangup()
    return str(response)


def say_then_ask(statement: str, prompt: str, hints: str | None = None) -> str:
    """Speak a statement, then ask a question in the same turn.

    Used to acknowledge what the caller said before asking the next question,
    which keeps the exchange from feeling like an interrogation.
    """
    response = VoiceResponse()
    response.say(statement, voice=settings.voice_tts_voice)
    gather = _gather(hints)
    gather.say(prompt, voice=settings.voice_tts_voice)
    response.append(gather)
    response.redirect(f"{GATHER_ACTION}?silent=1", method="POST")
    return str(response)
