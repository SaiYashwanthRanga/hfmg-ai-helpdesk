"""What the caller is probably about to say, for speech recognition.

Speech-to-text is far more accurate when it knows the vocabulary and the
shape of the answer: an email spelled letter by letter, ten digits, a yes or
no. Measured on the eval corpus (backend/eval), biasing lowered word error on
email and phone turns substantially -- see docs/reviews/VOICE_LATENCY_ACCURACY_REPORT.md.

Used by:
- `transcription_prompt(state)`: the `prompt` for OpenAI transcription (AI
  Call Simulator).
- `stt_prompt(state, collected)`: the same prompt for the phone path, returned
  to the SIP gateway as `stt_prompt` so it can pass it to its transcriber.
- `gather_hints(state)`: comma-separated recognizer hint phrases.
"""

# Words callers at HFMG say that a general-purpose recognizer gets wrong.
DOMAIN_TERMS = [
    "HFMG",
    "hfmg.net",
    "Horizon Family Medical Group",
    "eClinicalWorks",
    "eCW",
    "Outlook",
    "Microsoft Teams",
    "OneDrive",
    "SharePoint",
    "Excel",
    "VPN",
    "Wi-Fi",
    "MFA",
    "Authenticator",
    "password reset",
    "locked out",
    "printer",
    "scanner",
    "label printer",
    "shared drive",
    "front desk",
    "check in",
]

_STATE_HINTS: dict[str, str] = {
    "COLLECT_DESCRIPTION": "The caller describes a problem at a medical office -- either an IT issue or a patient service issue.",
    "CLASSIFY_CALLER_TYPE": "The caller says whether they need help with a patient service or an employee IT issue.",
    "COLLECT_DETAILS": "The caller says when the problem started and whether they can still work.",
    "COLLECT_NAME": "The caller says their first and last name and their department, for example: Maria Lopez, billing.",
    "COLLECT_PHONE": "The caller says a ten-digit US phone number, for example: 845-555-0142.",
    "CONFIRM_CALLBACK_NUMBER": "The caller answers yes or no, or says a different ten-digit US phone number.",
    "COLLECT_ALTERNATE_CALLBACK_NUMBER": "The caller says a ten-digit US phone number, for example: 845-555-0142.",
    "COLLECT_EMAIL": (
        "The caller says or spells an email address, usually at hfmg.net, "
        "for example: m lopez at h f m g dot net, which is mlopez@hfmg.net."
    ),
    # Spelling mode: the transcriber must write letters, not guess words. It
    # otherwise "corrects" unfamiliar names ("m, l, o, p, e, z" -> "MLOpec").
    "SPELL_NAME": (
        "The caller is spelling their name one letter at a time. Write every letter separately, "
        "separated by spaces, exactly as heard, for example: Y A S H W A N T H. Do not join the "
        "letters into a word and do not correct the spelling."
    ),
    "SPELL_EMAIL": (
        "The caller is spelling the part of their email address before the at sign, one letter at a "
        "time, and may say dot or underscore. Write every letter separately, separated by spaces, "
        "exactly as heard, for example: R A N G A dot S A I. Do not join letters into words."
    ),
    # Strict extraction's clarification questions (state COLLECT_DETAILS + a pending question).
    "CLARIFY_BLOCKED": "The caller answers yes or no, or says whether they can still work.",
    "CLARIFY_SCOPE": "The caller says whether anyone else is affected: just them, their team, or everyone.",
    "CLARIFY_PATIENT_CARE": "The caller answers yes or no about whether patients can be checked in or seen.",
    "CONFIRM_EMAIL": "The caller answers yes or no.",
    "CONFIRM_NAME": "The caller answers yes or no, or spells their name letter by letter.",
    "CONFIRM_SUMMARY": "The caller answers yes or no, or says what to change, such as their name, department, or when it started.",
    "CONFIRM_CATEGORY": "The caller answers yes or no.",
    "ANYTHING_ELSE": "The caller answers yes or no, or describes another problem.",
}

_STATE_GATHER_HINTS: dict[str, list[str]] = {
    "COLLECT_EMAIL": ["at hfmg dot net", "hfmg dot net", "dot net", "at", "dot", "underscore", "skip"],
    "COLLECT_PHONE": ["oh", "double"],
    "CLASSIFY_CALLER_TYPE": ["patient", "IT", "appointment", "computer", "employee", "portal"],
    "CONFIRM_CALLBACK_NUMBER": ["yes", "no", "that's right", "that's not right"],
    "COLLECT_ALTERNATE_CALLBACK_NUMBER": ["oh", "double"],
    "COLLECT_NAME": ["billing", "front desk", "radiology", "finance", "HR", "lab", "reception", "family medicine"],
    "CONFIRM_EMAIL": ["yes", "no", "correct", "that's right"],
    "CONFIRM_NAME": ["yes", "no", "that's right", "that's wrong"],
    "CONFIRM_SUMMARY": ["yes", "no", "that's right", "change", "department", "started"],
    "CONFIRM_CATEGORY": ["yes", "no"],
    "ANYTHING_ELSE": ["no that's all", "yes", "no"],
}


def transcription_prompt(state: str | None) -> str:
    """A short prompt: English only, vocabulary, then what this answer usually looks like."""
    hint = _STATE_HINTS.get(state or "", "")
    if state in ("SPELL_NAME", "SPELL_EMAIL"):
        # Vocabulary would pull letters toward words; spelling gets the rule only.
        return f"English phone call to the HFMG IT help desk. {hint}"
    return f"English phone call to the HFMG IT help desk. Vocabulary: {', '.join(DOMAIN_TERMS)}. {hint}".strip()


def recognition_mode(state: str | None, collected: dict | None) -> str | None:
    """Which prompt the next answer needs. Spelling turns get their own mode
    (and model): the email step always asks for spelling, and a name
    correction asks the caller to spell first and last name."""
    collected = collected or {}
    if state == "COLLECT_EMAIL":
        return "SPELL_EMAIL"
    if state == "CONFIRM_NAME" and collected.get("name_spell_step"):
        return "SPELL_NAME"
    if state == "COLLECT_DETAILS" and collected.get("pending_question"):
        return f"CLARIFY_{str(collected['pending_question']).upper()}"
    return state


def stt_prompt(state: str | None, collected: dict | None = None) -> str | None:
    """Transcription prompt for the caller's next answer, or None when the
    state has no specific hint (greeting, terminal states)."""
    mode = recognition_mode(state, collected)
    if mode not in _STATE_HINTS:
        return None
    return transcription_prompt(mode)


def gather_hints(state: str | None) -> str:
    """Comma-separated phrases for Twilio's <Gather hints>, state-specific first."""
    phrases = _STATE_GATHER_HINTS.get(state or "", []) + DOMAIN_TERMS
    return ", ".join(dict.fromkeys(phrases))
