"""Every line the voice agent speaks.

Kept in one module so IT and compliance can review and revise the wording
without touching conversation logic. See VOICE_AGENT_DESIGN.md section 2.
"""

GREETING = (
    "Thank you for calling Horizon Family Medical Group support. "
    "Please briefly tell me how I can help you today."
)

# --- who is calling: asked first (VOICE_ASK_CALLER_TYPE) --------------------------------------------
GREETING_IDENTIFY = "Thank you for calling the helpdesk. May I know if you are a patient or an employee?"
IDENTIFY_CLARIFY = "Sorry, I didn't catch that. Are you a patient, or an employee?"
IDENTIFY_AFTER_HELLO = "Hello. Are you a patient, or an employee?"
IDENTIFY_FALLBACK = "No problem. Please tell me briefly how I can help you today."
EMPLOYEE_ASK = "Thanks. Please briefly tell me what IT problem you're having."
TYPE_CORRECTED = "Okay, thanks for letting me know."

# --- patients: what they need. There is no appointment system behind this line: the agent collects
# the request and passes it to the support team, and says so; it never says anything was booked,
# changed, cancelled or found.
PATIENT_INTENT_ASK = "How can I help you today? Are you calling about booking an appointment or an existing appointment?"
PATIENT_INTENT_CLARIFY = (
    "Sorry, I want to be sure I help with the right thing. Is it about booking a new appointment, "
    "changing or cancelling one, or checking an existing appointment?"
)
PATIENT_INTENT_ASK_AGAIN = "What else can I help you with? Is it about booking an appointment or an existing appointment?"
PATIENT_DETAILS_ASK = {
    "BOOK": "Do you have a preferred day or time, or a particular doctor?",
    "RESCHEDULE": "Which appointment is it? The date and the doctor would help.",
    "CANCEL": "Which appointment is it? The date and the doctor would help.",
    "CHECK": "Which appointment are you asking about? The date and the doctor would help.",
    "TROUBLE": "What happens when you try to book? For example, an error message, or the page not loading.",
}
PATIENT_LIMITATION = (
    "I can't book, change, or look up appointments myself on this call, "
    "but I can pass your request to our team so they can call you back."
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

# Read-back, strict extraction: be honest about what was not settled.
SUMMARY_WORK_IMPACT_UNKNOWN = "I couldn't confirm how much this affects your work."
SUMMARY_FLAGGED_FOR_REVIEW = "I couldn't confirm the impact, so I'll flag it for a person to review."

# Strict extraction: the details question invites an explicit statement ("it's stopping me",
# "I can keep working") rather than a bare yes/no that could mean either half of the question.
DETAILS_ASK_STRICT_OPTIONS = (
    "When did this start, and are you able to keep working, or is it stopping you?",
    "When did this start, and is it stopping you from working, or can you keep going?",
)

# Clarification questions. The first wording is used the first time, the second if the
# first did not settle it. Each has one fixed meaning for yes (see nlu.CLARIFICATIONS).
CLARIFY_BLOCKED_OPTIONS = (
    "Is this stopping you from doing your work?",
    "Is it stopping you from working completely, yes or no?",
)
CLARIFY_SCOPE_OPTIONS = (
    "Is anyone else affected besides you?",
    "Is this only happening to you, or to other people too?",
)
CLARIFY_PATIENT_CARE_OPTIONS = (
    "Is this stopping you from checking in or seeing patients right now?",
    "Are patients being held up right now because of this, yes or no?",
)
CLARIFY_OPTIONS = {
    "blocked": CLARIFY_BLOCKED_OPTIONS,
    "scope": CLARIFY_SCOPE_OPTIONS,
    "patient_care": CLARIFY_PATIENT_CARE_OPTIONS,
}
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

# Callback number confirmation, before the ticket is filed.
CALLBACK_CONFIRM = (
    "I have your callback number as {number}. If our IT team needs to reach you regarding this ticket, "
    "they will call this number. Is this the best number to contact you on?"
)
# The same question for a patient call (caller_type PATIENT_SUPPORT): a patient is not talking to the IT
# team and has a request, not a ticket. The read-back of the number is the same.
PATIENT_CALLBACK_CONFIRM = (
    "I have your callback number as {number}. If our support team needs to reach you regarding your request, "
    "they will call this number. Is this the best number to contact you on?"
)
CALLBACK_ASK_OTHER = "What number would you like our IT team to call instead?"
# The same question for a patient call (caller_type PATIENT_SUPPORT).
PATIENT_CALLBACK_ASK_OTHER = "What number would you like our support team to call instead?"
CALLBACK_CONFIRM_NEW = "I have your callback number as {number}. Is that correct?"
CALLBACK_REPEAT = "Sorry, I didn't catch a yes or a no."
CALLBACK_KEEP_CURRENT = "No problem, we'll use {number}."

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
# A wrong read-back: find out which part, then ask for that part alone.
EMAIL_WHICH_PART = "Sorry about that. Was it the part before the at sign that was wrong, the part after it, or both?"
EMAIL_FIX_LOCAL = "Okay, just the part before the at sign. Please spell it slowly, one letter at a time."
EMAIL_FIX_DOMAIN = (
    "Okay, just the part after the at sign. Please say it, spelling the name one letter at a time, "
    "then dot and the ending."
)
EMAIL_FIX_AGAIN = "Sorry, I didn't catch that."
# An address already on file for a caller who confirmed who they are: offered, never assumed.
EMAIL_DIRECTORY_CONFIRM = "I have {email} on file for you. Is that still the best email for updates?"
# Asking for the name's spelling straight away when the recognizer was unsure of what it heard.
NAME_SPELL_LEAD = "I want to be sure I get your name right."

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
NAME_SPELL_FIRST_ASK = "Could you spell your first name for me, one letter at a time?"

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
GOODBYE = "Thank you for calling Horizon Family Medical Group. Goodbye."

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

# The same escalation lines for a caller who has not said (or been classified as) patient or employee yet,
# e.g. "can I talk to a person?" at the opening question. Neutral: no IT team, no ticket, no guessing who is
# calling, and no promise that a call has been arranged -- the request and number are passed on.
NEUTRAL_ESCALATION = (
    "Of course. I'll pass your request to our team with your number, {phone}, so they can call you back. "
    "One moment while I log that."
)
NEUTRAL_ESCALATION_PHONE_ASK = "Of course. What's the best number for our team to reach you on?"
NEUTRAL_ESCALATION_NO_NUMBER = "Of course. I'll pass your request to our team."
NEUTRAL_ESCALATION_MISUNDERSTOOD = (
    "I'm sorry, I'm having trouble understanding, and I don't want to keep you. "
    "I'll pass your request to our team with your number, {phone}, so they can call you back."
)
NEUTRAL_ESCALATION_NO_CALLBACK_NUMBER = (
    "I'm sorry, I'm having trouble understanding. I'll pass your request to our team."
)
NEUTRAL_ESCALATION_READ_BACK = "Your request number is {ticket_number}."

# Said only when nothing could be saved, so it must not promise a ticket or a callback.
SYSTEM_ERROR = (
    "I'm sorry, I'm having a technical problem on my end and wasn't able to save your request. "
    "Please try calling back in a few minutes."
)

# --- directory identity proposal (internal IT calls) ----------------------------------
# A directory match is only proposed; it counts for nothing until the caller says yes.
IDENTITY_CONFIRM = "I found a directory entry for {name}. Am I speaking with {name}?"
IDENTITY_REASK = "Sorry, I didn't catch that. Am I speaking with {name}? Please say yes or no."
IDENTITY_DECLINED = "Sorry about that."
IDENTITY_NOT_CONFIRMED = "That's okay."

# --- caller type classification (low confidence) ------------------------------------
CLASSIFY_CALLER_TYPE_ASK = (
    "Just so I can help you in the right way: are you calling about a patient service "
    "such as appointments or the patient portal, or is this about an employee IT issue?"
)

# --- patient support flow -----------------------------------------------------------
PATIENT_ACK_OPTIONS = (
    "I can help you with that.",
    "Let me get that noted for you.",
    "Okay, I'll get that logged for our team.",
)
PATIENT_NAME_ASK = "May I have your name, please?"
PATIENT_CREATING_TICKET = "Let me create a support request for you."
PATIENT_READ_BACK = (
    "Your request number is {ticket_number}. I've sent it to our support team "
    "and someone will follow up with you."
)
PATIENT_GOODBYE = "Thank you for calling Horizon Family Medical Group. Goodbye."
PATIENT_ANYTHING_ELSE = "Is there anything else I can help you with?"
PATIENT_ESCALATION = (
    "Of course. I'll have a member of our support team call you back at {phone}. "
    "One moment while I log that."
)
PATIENT_ESCALATION_NO_NUMBER = (
    "Of course. I'll ask a member of our support team to follow up with you."
)
# The rest of the escalation and leaving lines for a patient call: the employee lines with "support team"
# for "IT team" and "request" for "ticket". The read-back only gives the number: the line before it already
# says what happens next, and with no number to call nobody can promise to reach the caller.
PATIENT_ESCALATION_PHONE_ASK = "Of course. What's the best number for our support team to call you back on?"
PATIENT_ESCALATION_MISUNDERSTOOD = (
    "I'm sorry, I'm having trouble understanding, and I don't want to keep you. "
    "I'll have someone from our support team call you back at {phone} directly."
)
PATIENT_ESCALATION_NO_CALLBACK_NUMBER = (
    "I'm sorry, I'm having trouble understanding. I'll log a request for our support team to follow up."
)
PATIENT_ESCALATION_READ_BACK = "Your request number is {ticket_number}."
PATIENT_LEAVING_WITH_REQUEST = (
    "No problem. I've saved what you told me. Your request number is {ticket_number}. Take care."
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


_SAID_PUNCTUATION = {".": "dot", "_": "underscore", "-": "hyphen", "+": "plus", "%": "percent"}


def _spell(text: str) -> str:
    return " ".join(_SAID_PUNCTUATION.get(char, char) for char in text)


def spoken_email(email: str) -> str:
    """Render jsmith@hfmg.net as "j s m i t h at h f m g dot net".

    Every character is said, and punctuation as a word ("j.smith" is "j dot s m i t h"): a lone
    "." or "_" is a pause to the speech synthesizer, so the caller could not hear a wrong one.
    The domain name is spelled out but the ending (net, com) is spoken as a word, which is how
    people read addresses aloud.
    """
    local, _, domain = email.partition("@")
    parts = domain.split(".")
    spelled = [_spell(part) for part in parts[:-1]] + parts[-1:]
    return f"{_spell(local)} at {' dot '.join(spelled)}"
