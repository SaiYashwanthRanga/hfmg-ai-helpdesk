"""Who is calling, and what a patient wants, from what they said. Rule-based: no model call.

The agent's first question is "are you a patient or an employee?". Clear answers are decided here,
deterministically. Only an answer with no cue at all goes to the language model (in the
orchestrator), and an answer with cues for both sides is never guessed at: the caller is asked.

Caller types use the values already stored on tickets and sessions: PATIENT_SUPPORT is a patient,
INTERNAL_IT is an employee.
"""

import re

PATIENT = "PATIENT_SUPPORT"
EMPLOYEE = "INTERNAL_IT"

# What a patient asked for. Stored on the call as `patient_intent`.
BOOK, RESCHEDULE, CANCEL, CHECK, TROUBLE, OTHER = "BOOK", "RESCHEDULE", "CANCEL", "CHECK", "TROUBLE", "OTHER"

INTENT_LABELS = {
    BOOK: "book an appointment",
    RESCHEDULE: "reschedule an appointment",
    CANCEL: "cancel an appointment",
    CHECK: "check an existing appointment",
    TROUBLE: "difficulty booking an appointment",
    OTHER: "other question",
}


def _norm(text: str | None) -> str:
    lowered = (text or "").lower().replace("’", "'").replace("‘", "'")
    return " ".join(re.sub(r"[^a-z0-9' ]", " ", lowered).split())


# --- who is calling ------------------------------------------------------------------------------------

# "I'm a patient", "I am an employee", "as a patient", "I work at the clinic": the caller said who they are.
_SAYS_PATIENT = re.compile(r"\b(?:(?:i am|i'm|im|this is|calling as|as) (?:a |an )?(?:new |existing |current )?patient)\b")
_SAYS_EMPLOYEE = re.compile(
    r"\b(?:(?:i am|i'm|im|this is|calling as|as) (?:a |an )?(?:employee|staff member|member of (?:the )?staff)"
    r"|i (?:am )?(?:work|works|working)\b"
    r"|we work\b"
    r"|(?:i am|i'm|im)(?: calling)?(?: working)? (?:at|with|from) (?:the |our )?(?:clinic|office|practice|hfmg|horizon|hospital|front desk|billing|lab))"
)
_NOT_PATIENT = re.compile(r"\bnot (?:a |an )?patient\b")
_NOT_EMPLOYEE = re.compile(r"\bnot (?:a |an )?(?:employee|staff)\b")
# The bare word, said as an answer ("patient.", "employee"), or about someone ("a patient can't check in").
_WORD_PATIENT = re.compile(r"\bpatients?\b")
_WORD_EMPLOYEE = re.compile(r"\b(?:employees?|staff|co ?workers?|colleagues?)\b")
# What the call is about, when the caller did not say who they are.
_TOPIC_PATIENT = re.compile(
    r"\b(?:appointments?|appts?|re ?schedul\w*|schedul\w*|booking|book|see (?:the |a |my )?(?:doctor|dr|provider|physician)"
    r"|refills?|prescriptions?|test results?|lab results?|medical records?|patient portal|my portal|insurance|copay"
    r"|my bill|new patient)\b"
)
_TOPIC_EMPLOYEE = re.compile(
    r"\b(?:computer|laptop|printer|scanner|outlook|vpn|teams|wifi|wi fi|network|monitor|keyboard|workstation"
    r"|ecw|eclinicalworks|citrix|remote desktop|it (?:issue|problem|support))\b"
)


def identify_caller(utterance: str) -> str | None:
    """PATIENT_SUPPORT, INTERNAL_IT, or None when it is not clear (no cue, or cues for both sides).

    Order of evidence: what the caller said they are, then the bare words patient/employee, then
    what the call is about. At each step one side decides; both sides at once is "not clear"."""
    text = _norm(utterance)
    if not text:
        return None
    patient = bool(_SAYS_PATIENT.search(text)) or bool(_NOT_EMPLOYEE.search(text))
    employee = bool(_SAYS_EMPLOYEE.search(text)) or bool(_NOT_PATIENT.search(text))
    if patient != employee:
        return PATIENT if patient else EMPLOYEE
    if patient and employee:
        return None
    patient_word = bool(_WORD_PATIENT.search(text))
    employee_word = bool(_WORD_EMPLOYEE.search(text))
    patient_topic = bool(_TOPIC_PATIENT.search(text))
    employee_topic = bool(_TOPIC_EMPLOYEE.search(text))
    if patient_word != employee_word:
        # "patient" alone is a patient; "a patient can't check in because the computer is down" is not.
        if (patient_word and employee_topic) or (employee_word and patient_topic):
            return None
        return PATIENT if patient_word else EMPLOYEE
    if patient_word and employee_word:
        return None
    if patient_topic != employee_topic:
        return PATIENT if patient_topic else EMPLOYEE
    return None


def has_any_cue(utterance: str) -> bool:
    """Did the caller say anything this module can read? If not, the language model may be asked."""
    text = _norm(utterance)
    return any(
        pattern.search(text)
        for pattern in (_SAYS_PATIENT, _SAYS_EMPLOYEE, _NOT_PATIENT, _NOT_EMPLOYEE, _WORD_PATIENT, _WORD_EMPLOYEE, _TOPIC_PATIENT, _TOPIC_EMPLOYEE)
    )


def identity_statement(utterance: str) -> str | None:
    """Only an explicit "I'm a patient" / "I'm an employee" / "I work here" (never a topic or a bare
    word): used to notice a caller correcting themselves later in the call without mistaking a
    sentence about a patient, or about an appointment, for a change of who is calling."""
    text = _norm(utterance)
    patient = bool(_SAYS_PATIENT.search(text)) or bool(_NOT_EMPLOYEE.search(text))
    employee = bool(_SAYS_EMPLOYEE.search(text)) or bool(_NOT_PATIENT.search(text))
    if patient == employee:
        return None
    return PATIENT if patient else EMPLOYEE


_PROBLEM = re.compile(
    r"\b(?:not working|broken|down|jammed|crash\w*|froze\w*|frozen|slow|error|can'?t|cannot|won'?t|doesn'?t|isn'?t"
    r"|locked out|problem|issue|stuck|fails?|failed|internet)\b"
)


def states_it_problem(utterance: str) -> bool:
    """Did an employee also start on their IT problem ("I'm an employee, my printer is jammed")? Then that
    sentence is the description and they are not asked for it again. A sentence that only says more about
    who they are ("I'm Dr. Patel's nurse") is not."""
    text = _norm(utterance)
    return bool(_TOPIC_EMPLOYEE.search(text) or _PROBLEM.search(text))


# --- what a patient wants -----------------------------------------------------------------------------------

_APPOINTMENT = r"(?:appointments?|appts?|visit|visits)"
_CANCEL = re.compile(r"\bcancel(?:l?ing|led|ation)?\b|\bcall off\b")
_RESCHEDULE = re.compile(
    r"\bre ?schedul\w*|\bmove (?:my|the|an) " + _APPOINTMENT + r"\b|\bchange (?:my|the|an) " + _APPOINTMENT
    + r"\b|\b(?:different|another) (?:day|time|date)\b|\bchange the (?:day|time|date)\b|\bpush (?:it|my appointment) back\b"
    + r"|\bcancel\w*\b.{0,30}\b(?:and|then)\b.{0,20}\b(?:book|rebook|new one|another one)\b"
)
_TROUBLE_WORD = re.compile(
    r"\b(?:can'?t|cannot|can not|unable to|couldn'?t|not able to|trouble|difficulty|difficulties|problems?|issues?"
    r"|error|not working|won'?t|doesn'?t work|isn'?t working|stuck)\b"
)
_BOOKING_CONTEXT = re.compile(r"\b(?:book|booking|schedule|scheduling|" + _APPOINTMENT + r")\b")
_CHECK = re.compile(
    r"\b(?:check|confirm|verify|status|when is|what time is|what day is|do i have|did i (?:book|schedule|make)"
    r"|look up|lookup|find out)\b"
)
_APPOINTMENT_WORD = re.compile(r"\b" + _APPOINTMENT + r"\b")
_BOOK = re.compile(
    r"\bnew " + _APPOINTMENT + r"\b|\bsee (?:the |a |my )?(?:doctor|dr|provider|physician)\b"
    r"|\b(?:book|booking|booked|schedule|scheduling)\b"
    r"|\b(?:make|set up|setup|arrange|get|need|want|like)\b[^.]{0,25}\ban? " + _APPOINTMENT + r"\b"
)

# Patient requests that are not about an appointment, mapped to the patient categories that already exist.
_OTHER_CATEGORIES = (
    (re.compile(r"\brefills?\b|\bprescriptions?\b|\bmedications?\b"), "Prescription Refill Portal"),
    (re.compile(r"\bmedical records?\b|\btest results?\b|\blab results?\b|\brecords\b"), "Medical Records Portal"),
    (re.compile(r"\binsurance\b|\bbilling\b|\bcopay\b|\bmy bill\b|\bbill\b"), "Insurance / Billing Portal"),
    (re.compile(r"\blog ?in\b|\bpassword\b|\bpatient portal\b|\bportal\b"), "Patient Portal Login"),
    (re.compile(r"\bwebsite\b|\bweb site\b|\bsite\b"), "Website Error"),
)

APPOINTMENT_CATEGORY = "Appointment Booking"
DEFAULT_CATEGORY = "Other Patient Support"


def patient_intent(utterance: str) -> str | None:
    """What the patient wants, or None if it is not clear.

    BOOK, RESCHEDULE, CANCEL, CHECK (an existing appointment) or TROUBLE (difficulty booking).
    OTHER when the request is clearly not about an appointment (a refill, records, a bill). Two
    different appointment requests at once, or only the word "appointment", is None: ask."""
    text = _norm(utterance)
    if not text:
        return None
    if _TROUBLE_WORD.search(text) and _BOOKING_CONTEXT.search(text):
        return TROUBLE
    found = set()
    if _RESCHEDULE.search(text):
        found.add(RESCHEDULE)
    if _CANCEL.search(text):
        found.add(CANCEL)
    if _CHECK.search(text) and (_APPOINTMENT_WORD.search(text) or "when is my" in text):
        found.add(CHECK)
    if _BOOK.search(text):
        found.add(BOOK)
    if found == {RESCHEDULE, CANCEL} or found == {RESCHEDULE, BOOK} or found == {RESCHEDULE, CANCEL, BOOK}:
        return RESCHEDULE  # "cancel it and book another day" is a reschedule
    if len(found) == 1:
        return next(iter(found))
    if found:
        return None
    if _APPOINTMENT_WORD.search(text):
        return None  # about an appointment, but which kind: ask
    if any(pattern.search(text) for pattern, _ in _OTHER_CATEGORIES):
        return OTHER
    return None


def patient_category(intent: str, utterance: str) -> str:
    """The existing patient category for a request. Every appointment request is "Appointment Booking"."""
    if intent != OTHER:
        return APPOINTMENT_CATEGORY
    text = _norm(utterance)
    for pattern, category in _OTHER_CATEGORIES:
        if pattern.search(text):
            return category
    return DEFAULT_CATEGORY
