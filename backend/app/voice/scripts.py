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

NAME_ASK = "I understand you're having an issue with {issue}. May I have your name?"
NAME_ASK_NO_ISSUE = "May I have your name?"
NAME_RETRY = [
    "Sorry, could you say your first and last name again?",
    "One more time please, just your name.",
]

PHONE_ASK = "What's the best phone number for us to reach you?"
PHONE_RETRY = [
    "Could you say that number again, one digit at a time?",
    "Sorry, what's the best callback number, one digit at a time?",
]

EMAIL_ASK = "Thank you, {first_name}. What email address should we use for updates? You can say skip if you'd rather not."
EMAIL_ASK_NO_NAME = "What email address should we use for updates? You can say skip if you'd rather not."
EMAIL_CONFIRM = "I have {email}. Is that correct?"
EMAIL_RETRY = "Let's try once more. Could you spell out your email address for me?"
EMAIL_GIVE_UP = "No problem. We'll follow up by phone instead."

CATEGORY_CONFIRM = "Just to make sure I route this correctly, is this about {category}?"

PRIORITY_NOTICE = "Since this is affecting {impact}, I'm marking it {priority} priority."

CREATING_TICKET = "Let me create that ticket for you. One moment."
READ_BACK = "Your ticket number is {ticket_number}. I've sent it to our IT team and they'll follow up with you."

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


def spoken_email(email: str) -> str:
    """Render jsmith@hfmg.net as "j s m i t h at h f m g dot net".

    The domain name is spelled out but the TLD is spoken as a word, which is
    how people read addresses aloud.
    """
    local, _, domain = email.partition("@")
    parts = domain.split(".")
    spelled = [" ".join(part) for part in parts[:-1]] + parts[-1:]
    return f"{' '.join(local)} at {' dot '.join(spelled)}"
