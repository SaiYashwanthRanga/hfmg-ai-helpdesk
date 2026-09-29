"""Every line the voice agent speaks.

Kept in one module so IT and compliance can review and revise the wording
without touching conversation logic. See VOICE_AGENT_DESIGN.md section 2.
"""

GREETING = (
    "Thank you for calling Horizon Family Medical Group IT Help Desk. "
    "How can I assist you today?"
)

# Re-prompts are progressively simpler and never repeat the previous wording --
# a verbatim repeat is the clearest signal to a caller that they are stuck.
DESCRIPTION_ASK = "Sure. Can you tell me briefly what's going wrong?"
DESCRIPTION_RETRY = [
    "I'm sorry, I didn't catch that. Could you tell me briefly what's going wrong?",
    "I'm still having trouble hearing you. In a few words, what problem are you having?",
]

def pick(options: tuple[str, ...], n: int) -> str:
    """Rotate through equivalent wordings so a call doesn't sound like a form.

    Deterministic (the caller's turn count, not randomness) so a call is
    reproducible and tests can assert on it.
    """
    return options[n % len(options)]


# Spoken once, on the first question after the caller describes the problem,
# so they know they were heard. {issue} is a validated noun phrase
# ("your laptop's Bluetooth"); if the model gave none, the agent says nothing.
ACK_ISSUE_OPTIONS = (
    "Sorry to hear about {issue}.",
    "Okay, {issue}. Let's get that logged.",
    "Got it, {issue}.",
    "Thanks, I've got {issue} noted.",
)
ACK_ISSUE = ACK_ISSUE_OPTIONS[0]

# When it started and whether the caller can work drive priority, so they are
# asked -- once, together -- unless the caller already said them.
DETAILS_ASK_OPTIONS = (
    "When did this start, and can you still get your work done?",
    "When did this start, and is it stopping you from working?",
)
DETAILS_ASK = DETAILS_ASK_OPTIONS[1]
DETAILS_ASK_STARTED = "When did this start?"
DETAILS_ASK_BLOCKED = "Can you still get your work done, or is this stopping you completely?"

# Name and department in one question: one turn instead of two.
NAME_ASK_OPTIONS = (
    "Who am I speaking with, and which department are you in?",
    "May I have your name and department?",
)
NAME_ASK = NAME_ASK_OPTIONS[1]
NAME_ASK_NO_ISSUE = NAME_ASK
DEPARTMENT_ASK = "Thanks, {first_name}. And which department are you with?"
DEPARTMENT_ASK_NO_NAME = "Which department are you with?"
NAME_RETRY = [
    "Sorry, could you say your first and last name again?",
    "One more time please, just your name.",
]

PHONE_ASK = "What's the best number to reach you on?"
PHONE_RETRY = [
    "Could you say that number again, one digit at a time?",
    "Sorry, what's the best callback number, one digit at a time?",
]


def phone_retry(digits_heard: int, attempt: int) -> str:
    """Tell the caller what was wrong instead of asking them to repeat blindly.

    Seen in a real call: "11006", then "1-0-0-6", then a 12-digit number, each
    answered with the same "one digit at a time" and then an escalation.
    """
    if digits_heard == 0:
        return "I didn't catch a number there. Could you say it again, digit by digit?"
    if digits_heard < 10:
        return f"I only caught {digits_heard} digits, and I need all ten. Could you say the whole number again, slowly?"
    return f"That was {digits_heard} digits, and a phone number has ten. Could you say just the ten digits?"


PHONE_GIVE_UP = "That's okay, we'll follow up another way."

# Spelled, not said: transcription garbles spoken names in addresses
# ("saiyashwanth" -> "sichuan") but gets spelled letters right. Almost every
# caller is @hfmg.net, so only the part before the @ is needed.
EMAIL_ASK = (
    "Thanks, {first_name}. What's your HFMG email? Just spell the part before the at sign, "
    "or say skip."
)
EMAIL_ASK_NO_NAME = "What's your HFMG email? Just spell the part before the at sign, or say skip."
EMAIL_CONFIRM = "I have {email}. Is that correct?"
EMAIL_RETRY = "Let's try once more. Please spell the part before the at sign, slowly, one letter at a time."

# Names are read back spelled, because a misheard name sounds right when said
# back ("Yashwandh" is pronounced like "Yashwanth"). Only names we're unsure
# of are read back: see app/voice/names.py.
NAME_CONFIRM_OPTIONS = (
    "Just to be sure I have your name right: {spelled}. Is that correct?",
    "I want to get your name right. I have {spelled}. Is that correct?",
)
NAME_CONFIRM = NAME_CONFIRM_OPTIONS[0]
# After the caller corrected it: repeat the corrected name, never assume it.
NAME_RECONFIRM = "Thanks. So that's {spelled}. Is that right?"
NAME_ACCEPT_AS_SPOKEN = "Okay, I'll note it the way you spelled it."
NAME_SPELL_FIRST = "Sorry about that. Could you spell your first name for me, one letter at a time?"
NAME_SPELL_LAST = "Thanks. And your last name, one letter at a time?"

# --- the read-back before a ticket is created -------------------------------------
SUMMARY_INTRO_OPTIONS = (
    "Okay {first_name}, let me make sure I have this right.",
    "Alright {first_name}, here's what I have.",
)
SUMMARY_INTRO_NO_NAME = "Okay, let me make sure I have this right."
SUMMARY_UPDATED = "Okay, updated."
SUMMARY_CONFIRM_OPTIONS = ("Is that all correct?", "Is that right?", "Does that sound right?")
SUMMARY_WHAT_TO_CHANGE = "No problem. What should I change?"
SUMMARY_WHICH_PART = "Sorry, which part is wrong: your name, department, the problem, or when it started?"
SUMMARY_FILING = "Great, I'm filing that now."
SUMMARY_LEAVE_AS_IS = "Okay, I'll file it as it is and note that. Our team can sort out any details."
EMAIL_GIVE_UP = "No problem. We'll follow up by phone instead."

CATEGORY_CONFIRM = "Just to make sure I route this correctly, is this about {category}?"

CREATING_TICKET = "Let me create that ticket for you. One moment."
READ_BACK = "Your ticket number is {ticket_number}. I've sent it to our IT team and they'll follow up with you."
# The caller ended the call before we finished.
LEAVING_WITH_TICKET = "No problem. I've saved what you told me. Your ticket number is {ticket_number}. Take care."
LEAVING_NO_TICKET = "No problem. Take care."

ANYTHING_ELSE = "Is there anything else I can help you with?"
GOODBYE = "Thank you for calling Horizon Family Medical Group IT Help Desk. Goodbye."

ESCALATION_CALLER_REQUESTED = (
    "Of course. I'll have a member of our IT team call you back at {phone}. "
    "Let me get that request logged. One moment."
)
ESCALATION_MISUNDERSTOOD = (
    "I'm sorry, I'm having trouble understanding, and I don't want to keep you. "
    "I'll have someone from our IT team call you back at {phone} directly."
)
# Asked once when a caller wants a person but we have no number to call back.
ESCALATION_PHONE_ASK = "Of course. What's the best number for our IT team to call you back on?"
ESCALATION_CALLER_REQUESTED_NO_NUMBER = (
    "Of course. I'll ask a member of our IT team to follow up with you. "
    "Let me get that request logged. One moment."
)
ESCALATION_NO_CALLBACK_NUMBER = (
    "I'm sorry, I'm having trouble understanding. I'll log a request for our IT team to follow up."
)
ESCALATION_READ_BACK = (
    "Your callback request is ticket number {ticket_number}. Someone will reach out to you shortly."
)

SYSTEM_ERROR = (
    "I'm sorry, I'm having a technical problem on my end. "
    "I've logged a callback request and someone from IT will reach out to you shortly."
)


def spoken_ticket_number(ticket_number: str) -> str:
    """Render HFMG-2026-000482 as "H F M G, 2 0 2 6, 0 0 0 4 8 2".

    Spacing each character forces the TTS engine to read letters and digits
    individually instead of saying "four hundred eighty-two".
    """
    return ", ".join(" ".join(part) for part in ticket_number.split("-"))


def spoken_phone_number(phone: str) -> str:
    """Render +18455550142 as "8 4 5, 5 5 5, 0 1 4 2"."""
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        return " ".join(digits)
    return f"{' '.join(digits[:3])}, {' '.join(digits[3:6])}, {' '.join(digits[6:])}"


def spelled_name(name: str) -> str:
    """Render "Maria Lopez" as "M A R I A, L O P E Z" for a spelled read-back."""
    return ", ".join(" ".join(part.upper()) for part in name.split() if part)


def spoken_email(email: str) -> str:
    """Render jsmith@hfmg.net as "j s m i t h at h f m g dot net".

    The domain name is spelled out but the TLD is spoken as a word, which is
    how people read addresses aloud.
    """
    local, _, domain = email.partition("@")
    parts = domain.split(".")
    spelled = [" ".join(part) for part in parts[:-1]] + parts[-1:]
    return f"{' '.join(local)} at {' dot '.join(spelled)}"
