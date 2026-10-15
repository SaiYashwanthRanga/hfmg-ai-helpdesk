"""The conversation state machine.

Deliberately free of FastAPI and telephony imports, so
every transition can be unit tested by calling handle_turn directly.
See CALL_FLOW.md for the state diagram.

Voice-first rules this module enforces (docs/reviews/VOICE_CONVERSATION_REVIEW.md):
- never ask for something the caller already said;
- confirm only what is doubtful (an unfamiliar name, a spoken phone number,
  an email), and read the whole ticket back once before creating it;
- a correction is always repeated back, never silently applied;
- optional information (timing, department, email, name spelling, phone after
  two tries) never blocks the call or counts as a misunderstanding;
- the priority the agent announces and the reason it gives come from one rule
  (app/voice/priority.py), so they cannot contradict each other.
"""

import dataclasses
import difflib
import logging
import re
import uuid

from pydantic import EmailStr, TypeAdapter
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
    CallerType,
    Category,
    EscalationReason,
    Priority,
    Ticket,
    TicketSource,
    VoiceCallSession,
    VoiceCallState,
)
from app.schemas.ticket import TicketCreate
from app.services import ticket_service
from app.services.user_directory import LookupResult, LookupStatus, UserDirectoryService, normalize_extension
from app.voice import caller_routing, departments, directory_identity, diagnostics, names, nlu, reply, scripts
from app.voice import facts as facts_mod
from app.voice import priority as priority_rules
from app.services.ticket_text import DETAILS_MARKER, INTAKE_MARKER, TRANSCRIPT_MARKER
from app.voice.session import caller_id_is_usable, record_turn, transcript_text, update_collected

logger = logging.getLogger("hfmg.voice.orchestrator")

settings = get_settings()

CLASSIFICATION_CONFIDENCE_THRESHOLD = 0.80

#: Corrections to the read-back before the agent files it as-is and notes it.
MAX_SUMMARY_CORRECTIONS = 2
#: Read-backs of a name before the agent accepts the caller's own spelling.
MAX_NAME_ROUNDS = 2
#: Directory names proposed to the caller in one call (the caller-ID match, then at most one
#: more from an identifier they give) before the agent just asks who is calling.
MAX_IDENTITY_ROUNDS = 2
#: Re-asks of one proposal after an answer that was neither yes nor no.
MAX_IDENTITY_UNCLEAR = 1


class TurnOutcome:
    """What the caller hears next, plus any ticket work the route must finish."""

    def __init__(self, reply_: reply.Reply, ticket_id: uuid.UUID | None = None):
        self.lines = reply_.lines
        self.expect_reply = reply_.expect_reply
        self.ticket_id = ticket_id

    @property
    def hangup(self) -> bool:
        return not self.expect_reply

    @property
    def text(self) -> str:
        return " ".join(self.lines).strip()


def issue_filed(session: VoiceCallSession) -> bool:
    """Has a ticket been filed for the issue the caller is on right now?

    Not the same as "this call has a ticket": a caller may report several issues,
    and each gets its own. `ticket_filed` is cleared when the next one begins.
    A call that started before the flag existed falls back to `ticket_id`.
    """
    filed = (session.collected or {}).get("ticket_filed")
    if filed is None:
        return session.ticket_id is not None
    return bool(filed)


def _record_ticket(session: VoiceCallSession, ticket: Ticket) -> None:
    """Note a ticket filed on this call. `ticket_id` is always the latest; `ticket_ids` is all of them."""
    session.ticket_id = ticket.id
    update_collected(
        session,
        ticket_filed=True,
        ticket_ids=[*(session.collected.get("ticket_ids") or []), str(ticket.id)],
    )


def _is_patient_call(session: VoiceCallSession) -> bool:
    return (session.collected or {}).get("caller_type") == "PATIENT_SUPPORT"


async def start_call(
    session: VoiceCallSession,
    *,
    directory_lookup: LookupResult | None = None,
    identify_caller: bool | None = None,
) -> TurnOutcome:
    """Turn 0: greet, and ask who is calling (or, with the step off, wait for the problem).

    `directory_lookup` is the result of looking the caller ID up in the employee directory
    (None when there was no usable caller ID, or no directory is wired in). It is only kept on
    this call's session; the caller is asked to confirm a proposed name later, if the call turns
    out to be an employee IT call.

    `identify_caller` overrides VOICE_ASK_CALLER_TYPE for this call (the call simulator, which
    tests the employee IT intake, passes False).
    """
    if directory_lookup is not None:
        update_collected(session, directory=directory_identity.compact_lookup(directory_lookup))
    if settings.voice_ask_caller_type if identify_caller is None else identify_caller:
        session.state = VoiceCallState.IDENTIFY_CALLER
        record_turn(session, role="agent", text=scripts.GREETING_IDENTIFY)
        return TurnOutcome(_ask(session, scripts.GREETING_IDENTIFY))
    session.state = VoiceCallState.COLLECT_DESCRIPTION
    record_turn(session, role="agent", text=scripts.GREETING)
    return TurnOutcome(_ask(session, scripts.GREETING))


#: Marks a transcript line the recognizer produced from the agent's or its own prompt's words.
NOT_CALLER_SPEECH = "[not used: sounded like the agent or the speech recognizer, not the caller]"
#: What only the agent (or a recognizer echoing a help-desk prompt) says. A caller never answers a question with it.
_AGENT_VOICE = re.compile(
    r"\bhow (?:can|may) i (?:assist|help) you\b|\bthis is (?:the )?(?:hfmg|horizon)\b[^.?!]{0,40}\bhelp ?desk\b",
    re.IGNORECASE,
)


def _plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", (text or "").lower()))


def _not_caller_speech(session: VoiceCallSession, utterance: str) -> bool:
    """Is this "answer" really the agent's voice or the recognizer's prompt?

    Seen on a real call: during a silence (the caller looking up free disk space, then thinking about an
    extension) the transcriber returned "Hello, this is the HFMG IT help desk. How can I assist you today?" --
    words from its own prompt, not the caller. Only two things count: help-desk phrasing a caller does not use,
    or a near-exact copy (5+ words, 90% alike) of a whole line the agent just said -- its own audio leaking
    back. A caller repeating part of a question ("the part before the at sign") or spelling letters is never
    dropped here, however short or odd."""
    if not utterance:
        return False
    if _AGENT_VOICE.search(utterance):
        return True
    plain = _plain(utterance)
    if len(plain.split()) < 5:
        return False
    for turn in (session.turns or [])[-6:]:
        if turn.get("role") != "agent":
            continue
        other = _plain(turn.get("text") or "")
        if other and difflib.SequenceMatcher(None, plain, other).ratio() >= 0.9:
            return True
    return False


_GOODBYE_STATES = (
    VoiceCallState.IDENTIFY_CALLER,
    VoiceCallState.PATIENT_INTENT,
    VoiceCallState.PATIENT_DETAILS,
    VoiceCallState.COLLECT_DESCRIPTION,
    VoiceCallState.CLASSIFY_CALLER_TYPE,
    VoiceCallState.COLLECT_DETAILS,
    VoiceCallState.COLLECT_DIAGNOSTICS,
    VoiceCallState.CONFIRM_IDENTITY,
    VoiceCallState.COLLECT_NAME,
    VoiceCallState.CONFIRM_NAME,
    VoiceCallState.COLLECT_PHONE,
    VoiceCallState.CONFIRM_CALLBACK_NUMBER,
    VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER,
    VoiceCallState.COLLECT_EXTENSION,
    VoiceCallState.COLLECT_EMAIL,
    VoiceCallState.CONFIRM_EMAIL,
    VoiceCallState.CONFIRM_SUMMARY,
    VoiceCallState.CONFIRM_CATEGORY,
    VoiceCallState.ANYTHING_ELSE,
)


async def handle_turn(
    db: AsyncSession,
    session: VoiceCallSession,
    *,
    utterance: str,
    confidence: float | None = None,
    directory: UserDirectoryService | None = None,
) -> TurnOutcome:
    """Interpret one caller utterance and advance the conversation.

    `directory` is only used to look up an identifier the caller gives while confirming who
    they are; without it that step simply treats the answer as "no"."""
    utterance = (utterance or "").strip()
    if _not_caller_speech(session, utterance):
        # The recognizer returned the agent's (or its own prompt's) words, not the caller's: a silence. Kept
        # on the transcript, marked, and never stored as an answer to anything.
        logger.warning("Call %s: answer in %s matched the agent's or recognizer's own words; treated as silence",
                       session.call_id, session.state.value)
        record_turn(session, role="caller", text=f"{NOT_CALLER_SPEECH} {utterance}", confidence=confidence)
        utterance = ""
    else:
        record_turn(session, role="caller", text=utterance, confidence=confidence)

    if session.state in (VoiceCallState.ESCALATED, VoiceCallState.COMPLETED):
        # Replay of an already-terminal turn (gateway retry). Say goodbye again
        # rather than re-running any of the work.
        return TurnOutcome(_speak(session, reply.say_and_hangup(scripts.GOODBYE)))

    if not utterance:
        if session.collected.get("escalation_pending"):
            return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
        if session.state == VoiceCallState.CONFIRM_SUMMARY:
            # Silence after the read-back is not a yes (and may be a dropped line). As before, the ticket is filed
            # as it is -- but recorded as read back and NOT confirmed, never as confirmed (or as "not correct").
            update_collected(session, summary_unanswered=True, summary_confirmed=False, summary_awaiting_change=False)
            return await _create_ticket_and_read_back(db, session)
        if session.state == VoiceCallState.CLASSIFY_CALLER_TYPE:
            _apply_classification(session, nlu.CallerClassification("INTERNAL_IT", 0.60))
            return await _next_step(db, session)
        if session.state == VoiceCallState.CONFIRM_IDENTITY:
            return await _identity_unclear(db, session)  # silence is not a yes
        if session.state == VoiceCallState.IDENTIFY_CALLER:
            return await _identify_unclear(db, session)  # silence is not an answer, and is not a failure
        if session.state == VoiceCallState.PATIENT_INTENT:
            return await _handle_patient_intent(db, session, "")
        if session.state == VoiceCallState.PATIENT_DETAILS:
            return await _next_step(db, session)  # the question is optional: move on
        if session.state in (VoiceCallState.COLLECT_DETAILS, VoiceCallState.CONFIRM_NAME):
            # Optional information / confirmation: silence moves the call on
            # rather than counting as a misunderstanding.
            update_collected(session, name_spell_step=None, name_reconfirm=False, name_confirm_pending=False)
            if session.collected.get("pending_question"):
                _record_clarification(session, session.collected["pending_question"], "unclear", "")
            return await _next_step(db, session)
        if session.state == VoiceCallState.COLLECT_PHONE:
            return await _phone_not_understood(db, session, "")
        if session.state == VoiceCallState.COLLECT_EXTENSION:
            return await _handle_extension(db, session, "", directory)  # optional: never a misunderstanding
        if session.state == VoiceCallState.COLLECT_DIAGNOSTICS:
            return await _handle_diagnostics(db, session, "")  # unanswered, not a misunderstanding
        return await _handle_failure(db, session)

    if session.state in _GOODBYE_STATES and nlu.is_goodbye(utterance):
        # "My extension is 3006, thanks, bye": what the caller said on the way out is kept first.
        noted = _extension_before_leaving(session, utterance, directory)
        leaving = await _caller_leaving(db, session)
        return _lead_with(session, leaving, noted) if noted else leaving

    late = _maybe_late_server(session, utterance)
    if late is not None:
        return late
    late, extension_noted, utterance = _take_late_extension(session, utterance, directory)
    if late is not None:
        return late
    if extension_noted and (nlu.is_goodbye(utterance) or _only_goodbye(utterance)):
        # A longer "... and my extension is 3006, thanks, bye bye": the extension is recorded, then the call ends.
        return _lead_with(session, await _caller_leaving(db, session), extension_noted)
    if not extension_noted:
        renamed = await _maybe_name_change(db, session, utterance)
        if renamed is not None:
            return renamed

    corrected = await _maybe_switch_caller_type(db, session, utterance)
    if corrected is not None:
        return corrected

    handlers = {
        VoiceCallState.IDENTIFY_CALLER: _handle_identify_caller,
        VoiceCallState.PATIENT_INTENT: _handle_patient_intent,
        VoiceCallState.PATIENT_DETAILS: _handle_patient_details,
        VoiceCallState.COLLECT_DESCRIPTION: _handle_description,
        VoiceCallState.CLASSIFY_CALLER_TYPE: _handle_caller_type_clarification,
        VoiceCallState.COLLECT_DETAILS: _handle_details,
        VoiceCallState.COLLECT_DIAGNOSTICS: _handle_diagnostics,
        VoiceCallState.CONFIRM_IDENTITY: lambda d, s, u: _handle_identity_confirmation(d, s, u, directory),
        VoiceCallState.COLLECT_NAME: _handle_name,
        VoiceCallState.CONFIRM_NAME: _handle_name_confirmation,
        VoiceCallState.COLLECT_PHONE: _handle_phone,
        VoiceCallState.CONFIRM_CALLBACK_NUMBER: _handle_callback_confirmation,
        VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER: _handle_alternate_callback,
        VoiceCallState.COLLECT_EXTENSION: lambda d, s, u: _handle_extension(d, s, u, directory),
        VoiceCallState.COLLECT_EMAIL: _handle_email,
        VoiceCallState.CONFIRM_EMAIL: _handle_email_confirmation,
        VoiceCallState.CONFIRM_SUMMARY: _handle_summary_confirmation,
        VoiceCallState.CONFIRM_CATEGORY: _handle_category_confirmation,
        VoiceCallState.ANYTHING_ELSE: _handle_anything_else,
    }
    handler = handlers.get(session.state)
    if handler is None:
        logger.error("No handler for state %s on call %s", session.state, session.call_id)
        return await escalate(db, session, EscalationReason.SYSTEM_ERROR)

    outcome = await handler(db, session, utterance)
    return _lead_with(session, outcome, extension_noted) if extension_noted else outcome


# --- entity confidence ------------------------------------------------------------


def _last_caller_confidence(session: VoiceCallSession) -> float | None:
    """Recognizer confidence for the caller's latest utterance, when the speech
    provider reports one (the SIP gateway does; the simulator when logprobs exist)."""
    for turn in reversed(session.turns or []):
        if turn.get("role") == "caller":
            value = turn.get("confidence")
            return float(value) if isinstance(value, (int, float)) else None
    return None


def _set_name(
    session: VoiceCallSession, name: str, *, model_confidence: str | None = None, spelled: bool = False
) -> bool:
    """Record the caller's name and decide whether it needs reading back. False (nothing recorded) for something
    that is plainly not a name ("Name", "Hi name"): it is never stored or read back to the caller.

    Only names we are unsure of cost the caller a turn (app/voice/names.py).
    """
    if not names.plausible(name):
        logger.info("Call %s: a reading of the name that is not a name was dropped", session.call_id)
        return False
    stt_confidence = _last_caller_confidence(session)
    confidence = names.name_confidence(
        name,
        spelled=spelled,
        model_confidence=model_confidence,
        stt_confidence=stt_confidence,
    )
    already_confirmed = (session.collected.get("name_confirmed_as") or "").lower() == name.lower()
    pending = bool(settings.voice_confirm_name and confidence == "low" and not already_confirmed)
    update_collected(
        session,
        caller_name=name,
        name_confidence=confidence,
        name_confirm_pending=pending,
        # The recognizer itself was unsure what it heard: ask for the spelling now, don't read back a guess.
        name_spell_first=pending
        and names.needs_spelling(
            name, spelled=spelled, model_confidence=model_confidence, stt_confidence=stt_confidence
        ),
    )
    return True


#: Words that make a department name part of a system's name ("the billing app"), not the caller's department.
_SYSTEM_NOUNS = r"(?:app|apps|application|system|software|portal|server|program|report|reports|module|screen|queue|" \
                r"printer|drive|folder|share|email|account|website|site|computer|pc|machine|scanner)"
#: The caller saying where they work: "I'm in billing", "this is James from the front desk", "billing department".
_OWN_DEPARTMENT = re.compile(
    r"\b(?:i'?m|i am|we'?re|we are|i work|we work|working|calling)\s+(?:in|with|for|at|on|from|over in)\b"
    r"|\bthis is\b[^.?!]{0,40}?\b(?:from|in|with|at)\b"
    r"|\b(?:department|dept)\b",
    re.IGNORECASE,
)


#: Words with which a caller says others are affected, or that it is only them. "Several people" or "the whole
#: site" is recorded only when the caller's own words say so -- never from how the model read the problem.
_SCOPE_MANY = ("everyone", "everybody", "whole team", "the team", "our team", "my team", "all of us", "others",
               "other people", "colleagues", "coworkers", "co-workers", "several", "a few of us", "whole office",
               "whole clinic", "whole site", "entire", "nobody can", "no one can", "we all", "all of our", "all our",
               "rest of", "the office", "people here")
_SCOPE_ONE = ("just me", "only me", "only mine", "just mine", "nobody else", "no one else", "only my", "just my",
              "only one")


def _scope_stated(utterance: str, scope: str | None) -> str | None:
    """The model's reading of who is affected, kept only when the caller's words support it."""
    text = (utterance or "").lower()
    if scope in ("several_people", "whole_site") and any(w in text for w in _SCOPE_MANY):
        return scope
    if scope == "one_person" and any(w in text for w in _SCOPE_ONE):
        return scope
    return None


def _last_caller_words(session: VoiceCallSession) -> str:
    return next((t.get("text") or "" for t in reversed(session.turns or []) if t.get("role") == "caller"), "")


def _department_mentions(utterance: str, department: str) -> list[re.Match]:
    """Where the caller said this department: as given, by its canonical name, or by one of its aliases
    ("HR" for Human Resources)."""
    text = (utterance or "").lower()
    match = departments.canonicalize(department)
    canonical = match.name if match else None
    words = {w for w in {(department or "").lower(), (canonical or "").lower()} if w}
    words |= {alias for alias, target in departments.ALIASES.items() if canonical and target == canonical}
    return [m for w in words for m in re.finditer(rf"\b{re.escape(w)}\b", text)]


def _only_a_system_name(utterance: str, department: str) -> bool:
    """True when every mention of the department is part of a system's name ("the billing application",
    "the HR portal"), so it says nothing about where the caller works."""
    text = (utterance or "").lower()
    mentioned = _department_mentions(text, department)
    return bool(mentioned) and all(
        re.match(rf"\s*(?:\w+\s+)?{_SYSTEM_NOUNS}\b", text[m.end():]) for m in mentioned
    )


def _department_stated(utterance: str, department: str) -> bool:
    """Did the caller say this is *their* department? A department taken from the problem description is only
    kept when they did ("I'm in billing", "James from the front desk"). A department word that is part of an
    application or system name ("the billing app is failing") never makes it the caller's department: the
    department question is asked instead. Never guessed from what the problem is about."""
    text = (utterance or "").lower()
    if not _department_mentions(text, department) or _only_a_system_name(text, department):
        return False
    return bool(_OWN_DEPARTMENT.search(text))


def _set_department(session: VoiceCallSession, raw: str | None) -> None:
    """Map what the caller said onto HFMG's departments; flag it when it doesn't match."""
    match = departments.canonicalize(raw)
    if match is not None:
        update_collected(session, department=match.name, department_verified=match.verified)


# --- state handlers -------------------------------------------------------


# --- who is calling, and the patient request flow -----------------------------------------------------------
#
# The call opens with "are you a patient or an employee?". A patient is taken through a short request
# flow (what they need, one optional question, name, callback number) and the request is filed as a
# patient support request for the team to call back about. There is no appointment system behind this
# line, so nothing is ever booked, changed, cancelled or looked up, and the agent says so. An employee
# goes straight into the existing IT flow, unchanged. Caller type is kept per call, in `collected`, and
# only the states of its own flow can be reached: patient states never run the IT language model or
# create an IT ticket, and the IT states never run patient handling.

_PATIENT_STATES = (VoiceCallState.PATIENT_INTENT, VoiceCallState.PATIENT_DETAILS)
_NO_DETAILS = {"no", "nope", "none", "nothing", "skip", "i don't know", "i dont know", "not sure", "no idea", "don't know"}


def _with(acknowledgement: str | None, prompt: str) -> str:
    return f"{acknowledgement} {prompt}" if acknowledgement else prompt


async def _route_caller(
    db: AsyncSession, session: VoiceCallSession, utterance: str, caller_type: str, *, acknowledgement: str | None = None
) -> TurnOutcome:
    """The caller type is known: store it and start that flow. If the same sentence already carries the
    request ("I'm an employee, my printer is jammed", "I need to cancel my appointment") it is used,
    so the caller is never asked for it twice."""
    update_collected(session, identified_by_routing=True, identify_attempts=0)
    _apply_classification(session, nlu.CallerClassification(caller_type, 1.0))
    if caller_type == caller_routing.PATIENT:
        intent = caller_routing.patient_intent(utterance)
        if intent:
            return await _set_patient_intent(db, session, intent, utterance, acknowledgement=acknowledgement)
        session.state = VoiceCallState.PATIENT_INTENT
        return TurnOutcome(_speak(session, _ask(session, _with(acknowledgement, scripts.PATIENT_INTENT_ASK))))
    session.state = VoiceCallState.COLLECT_DESCRIPTION
    if caller_routing.states_it_problem(utterance):
        return await _handle_description(db, session, utterance)
    return TurnOutcome(_speak(session, _ask(session, _with(acknowledgement, scripts.EMPLOYEE_ASK))))


#: "Hello?" answers to the opening question that are just asked again; more count as unclear (bounded).
MAX_IDENTIFY_HELLOS = 2


async def _handle_identify_caller(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    if nlu.mentions_escalation(utterance):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    caller_type = caller_routing.identify_caller(utterance)
    if caller_type:
        return await _route_caller(db, session, utterance, caller_type)
    if nlu.is_greeting(utterance):  # "Hello?": ask again, without counting it as an unclear answer -- twice
        hellos = session.collected.get("identify_hellos", 0) + 1
        update_collected(session, identify_hellos=hellos)
        if hellos <= MAX_IDENTIFY_HELLOS:
            return TurnOutcome(_speak(session, _ask(session, scripts.IDENTIFY_AFTER_HELLO)))
    if not caller_routing.has_any_cue(utterance) and len(utterance.split()) >= 2:
        # Nothing the rules can read ("I'm Dr. Patel's nurse"): the language model may know. Cues for both
        # sides are never sent here: that is a question for the caller, not a guess.
        classification = await nlu.classify_caller_type(utterance)
        if classification.confidence >= CLASSIFICATION_CONFIDENCE_THRESHOLD:
            return await _route_caller(db, session, utterance, classification.caller_type)
    return await _identify_unclear(db, session)


async def _identify_unclear(db: AsyncSession, session: VoiceCallSession) -> TurnOutcome:
    """Not clear who is calling: ask once more. If it still is not, carry on as the older flow did (describe
    the problem, and the type is worked out from that) rather than leave the caller stuck."""
    attempts = session.collected.get("identify_attempts", 0) + 1
    update_collected(session, identify_attempts=attempts)
    if attempts >= 2:
        update_collected(session, identify_attempts=0)
        session.state = VoiceCallState.COLLECT_DESCRIPTION
        return TurnOutcome(_speak(session, _ask(session, scripts.IDENTIFY_FALLBACK)))
    session.state = VoiceCallState.IDENTIFY_CALLER
    return TurnOutcome(_speak(session, _ask(session, scripts.IDENTIFY_CLARIFY)))


async def _maybe_switch_caller_type(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome | None:
    """A caller who says "actually, I'm a patient" (or an employee) at the start of the other flow is
    moved to the right one. Only an explicit "I'm a ..." counts, only before anything was filed, and
    only while the other flow is still at its first question: a sentence that merely mentions a patient
    or an appointment never changes who is calling."""
    c = session.collected
    if not c.get("identified_by_routing") or c.get("ticket_ids"):
        return None
    first_question = session.state in _PATIENT_STATES or (
        session.state == VoiceCallState.COLLECT_DESCRIPTION and not c.get("description")
    )
    said = caller_routing.identity_statement(utterance) if first_question else None
    if said is None or said == c.get("caller_type"):
        return None
    update_collected(session, **{key: None for key in (
        "patient_intent", "patient_details", "patient_category", "patient_intent_tries", "patient_limitation_told",
        "description", "short_issue", "category", "category_confidence", "started", "work_blocked",
        "affected_scope", "patient_care_affected", "model_priority", "pending_question",
    )})
    return await _route_caller(db, session, utterance, said, acknowledgement=scripts.TYPE_CORRECTED)


def _limitation(session: VoiceCallSession, acknowledgement: str | None) -> str | None:
    """Say once, before asking more of a patient, what this line cannot do."""
    if session.collected.get("patient_limitation_told"):
        return acknowledgement
    update_collected(session, patient_limitation_told=True)
    return _with(acknowledgement, scripts.PATIENT_LIMITATION)


async def _set_patient_intent(
    db: AsyncSession, session: VoiceCallSession, intent: str, utterance: str, *, acknowledgement: str | None = None
) -> TurnOutcome:
    words = " ".join((utterance or "").split())[:300]
    description = f"Patient request: {caller_routing.INTENT_LABELS[intent]}."
    if words:
        description = f'{description} Caller said: "{words}"'
    update_collected(
        session,
        patient_intent=intent,
        patient_category=caller_routing.patient_category(intent, utterance),
        description=description,
        patient_details=None,
    )
    question = scripts.PATIENT_DETAILS_ASK.get(intent)
    if question:
        session.state = VoiceCallState.PATIENT_DETAILS
        return TurnOutcome(_speak(session, _ask(session, _with(_limitation(session, acknowledgement), question))))
    return await _next_step(db, session, acknowledgement=_limitation(session, acknowledgement))


async def _handle_patient_intent(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    if nlu.mentions_escalation(utterance):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    intent = caller_routing.patient_intent(utterance)
    if intent:
        return await _set_patient_intent(db, session, intent, utterance)
    tries = session.collected.get("patient_intent_tries", 0) + 1
    update_collected(session, patient_intent_tries=tries)
    if tries <= 1:
        session.state = VoiceCallState.PATIENT_INTENT
        return TurnOutcome(_speak(session, _ask(session, scripts.PATIENT_INTENT_CLARIFY)))
    # Still not clear: keep the request in the caller's own words for the team. Nothing is guessed.
    return await _set_patient_intent(db, session, caller_routing.OTHER, utterance)


async def _handle_patient_details(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    """One optional question about the request (which appointment, a preferred time). Whatever the caller
    says is kept as they said it; "no" or "I don't know" simply moves on."""
    if nlu.mentions_escalation(utterance):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    text = " ".join((utterance or "").split())
    if text and text.lower().strip(" .!,") not in _NO_DETAILS:
        details = text[:300]
        update_collected(
            session, patient_details=details, description=f"{session.collected.get('description') or ''} Details: {details}".strip()
        )
    return await _next_step(db, session)


async def _handle_description(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    if nlu.is_greeting(utterance) and not session.collected.get("greeted"):
        # "Hi." / "Hello, is this IT?" is a greeting, not a failed answer:
        # invite the problem without counting a misunderstanding.
        update_collected(session, greeted=True)
        return TurnOutcome(_speak(session, _ask(session, scripts.DESCRIPTION_ASK)))

    result = await nlu.interpret_description(utterance)

    if _wants_human(utterance, result):
        if not result.failed:
            # "My password was reset and I can't fix it -- have IT call me":
            # keep the problem on the callback ticket.
            _record_description(session, result)
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    if result.failed:
        return await _handle_failure(db, session)

    _record_description(session, result)

    if not session.collected.get("caller_type"):
        classification = await nlu.classify_caller_type(result.value or utterance)
        if classification.confidence >= CLASSIFICATION_CONFIDENCE_THRESHOLD:
            _apply_classification(session, classification)
        else:
            update_collected(session, classification_pending=True)
            ack = _acknowledgement(session)
            session.state = VoiceCallState.CLASSIFY_CALLER_TYPE
            prompt = scripts.CLASSIFY_CALLER_TYPE_ASK
            if ack:
                prompt = f"{ack} {prompt}"
            return TurnOutcome(_speak(session, _ask(session, prompt)))
    elif _is_patient_call(session):
        # Second issue on the same call: re-classify the patient subcategory.
        classification = await nlu.classify_caller_type(result.value or utterance)
        if classification.patient_category:
            update_collected(session, patient_category=classification.patient_category)

    ack = _patient_ack(session) if _is_patient_call(session) else _acknowledgement(session)
    return await _next_step(db, session, acknowledgement=ack)


def _acknowledgement(session: VoiceCallSession) -> str | None:
    """"Sorry to hear about your laptop's Bluetooth." -- or nothing, if the
    model gave no usable phrase. Never a generic "I understand you're having an issue"."""
    issue = session.collected.get("short_issue")
    if not issue:
        return None
    return scripts.pick(scripts.ACK_ISSUE_OPTIONS, len(session.turns or [])).format(issue=issue)


def _record_description(session: VoiceCallSession, result: nlu.TurnResult) -> None:
    extras = result.extras
    collected = session.collected
    strict_facts = extras.get("facts")
    # Strict extraction records the three impact facts through the facts engine
    # (with evidence and source); the original path writes the plain values.
    impact = {} if strict_facts else dict(
        work_blocked=extras.get("work_blocked"),
        affected_scope=_scope_stated(_last_caller_words(session), extras.get("affected_scope")),
        patient_care_affected=extras.get("patient_care_affected"),
    )
    update_collected(
        session,
        description=result.value,
        category=result.category,
        category_confidence=result.confidence,
        model_priority=(result.priority or Priority.MEDIUM).value,
        short_issue=extras.get("short_issue"),
        started=extras.get("started") or nlu.quick_started(_last_caller_words(session)),
        details=None,  # belongs to the previous issue if the caller reports another
        **impact,
    )
    if strict_facts:
        session.collected = facts_mod.apply_facts(session.collected, list(strict_facts.values()))
        update_collected(session, patient_context_mentioned=bool(extras.get("patient_context_mentioned")))
    # Volunteered details: keep what the caller already told us (a second
    # issue on the same call keeps name and department), fill the gaps.
    if not collected.get("caller_name") and extras.get("caller_name"):
        _set_name(session, extras["caller_name"], model_confidence=extras.get("name_confidence"))
    if not collected.get("department") and extras.get("department") and _department_stated(
        _last_caller_words(session), extras["department"]
    ):
        _set_department(session, extras["department"])
    _apply_priority_rules(session)


def _apply_classification(session: VoiceCallSession, classification: nlu.CallerClassification) -> None:
    update_collected(
        session,
        caller_type=classification.caller_type,
        classification_pending=False,
    )
    if classification.caller_type == "PATIENT_SUPPORT" and classification.patient_category:
        update_collected(session, patient_category=classification.patient_category)


async def _handle_caller_type_clarification(
    db: AsyncSession, session: VoiceCallSession, utterance: str
) -> TurnOutcome:
    if _wants_human(utterance, nlu.TurnResult()):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    classification = await nlu.interpret_caller_type_answer(utterance)
    _apply_classification(session, classification)
    return await _next_step(db, session, acknowledgement=_patient_ack(session))


def _patient_ack(session: VoiceCallSession) -> str | None:
    if _is_patient_call(session):
        return scripts.pick(scripts.PATIENT_ACK_OPTIONS, len(session.turns or []))
    return _acknowledgement(session)


async def _handle_details(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    """When it started / whether the caller can work. Optional: never retried."""
    if session.collected.get("pending_question"):
        return await _handle_clarification(db, session, utterance)
    asked = _details_question(session)
    asked_when = not session.collected.get("started")  # the question asked when it started
    result = await nlu.interpret_details(utterance, asked=asked)

    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    _keep_details(session, result, utterance)
    detail_facts = result.extras.get("facts")
    if detail_facts:
        # Strict extraction: unknown facts never overwrite what is already known.
        if result.extras.get("started"):
            update_collected(session, started=result.extras["started"])
        session.collected = facts_mod.apply_facts(session.collected, list(detail_facts.values()))
        _apply_priority_rules(session)
    elif not result.failed or result.extras.get("work_blocked") is not None:
        values = {
            "started": result.extras.get("started"),
            "work_blocked": result.extras.get("work_blocked"),
            "affected_scope": _scope_stated(utterance, result.extras.get("affected_scope")),
        }
        update_collected(session, **{k: v for k, v in values.items() if v is not None})
        _apply_priority_rules(session)
    _started_from_words(session, utterance, answers_when=asked_when)
    _work_impact_from_words(session, utterance)
    return await _next_step(db, session)


def _started_from_words(session: VoiceCallSession, utterance: str, *, answers_when: bool = False) -> None:
    """The caller said when it started but no start time was recorded: keep what they said.

    Seen on a test call: "it was working yesterday and expired today" left the ticket with no start time, and
    the question that asks for it is only asked once. Only fills a gap -- a start time already recorded (by the
    model, or a correction at the read-back) is never overwritten."""
    if session.collected.get("started") or _is_patient_call(session):
        return
    said = nlu.quick_started(utterance, answers_when=answers_when)
    if said:
        update_collected(session, started=said)


def _work_impact_from_words(session: VoiceCallSession, utterance: str) -> None:
    """The caller said plainly whether they can work ("..., but I can still work") while answering something else.

    Seen on a test call: the model read "it's not working" (the device) as "can't work", the caller later said "I can
    still work", and the ticket was filed High because "you can't work". What the caller says about their own work
    is recorded as their answer -- it outranks a reading of the description -- and the priority is decided again."""
    if _is_patient_call(session):
        return
    said = nlu.quick_work_impact(utterance)
    if said is None or session.collected.get("work_blocked") is said:
        return
    session.collected = facts_mod.apply_fact(session.collected, facts_mod.Fact.known(
        facts_mod.WORK_BLOCKED, said, source=facts_mod.Source.ANSWER, confidence=0.95, evidence=utterance.strip()[:200],
    ))
    _apply_priority_rules(session)


_MIN_FALLBACK_WORDS = 4


def _keep_details(session: VoiceCallSession, result: nlu.TurnResult, utterance: str) -> None:
    """Keep what the caller said about the problem, so it does not live only in the transcript.

    The model reports it in the same call that reads the start time. If that call
    failed outright (no extras at all) the caller's own words are kept instead, when
    there are enough of them to be more than "yes" or "this morning".
    """
    if "details" in result.extras:
        details = result.extras["details"]
    elif len(utterance.split()) >= _MIN_FALLBACK_WORDS:
        details = " ".join(utterance.split())
    else:
        details = None
    if details:
        update_collected(session, details=details[:2000])


async def _handle_name(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    name_known = bool(session.collected.get("caller_name"))
    if name_known:
        known = _department_only(utterance)
        if known is not None:
            update_collected(session, department=known.name, department_verified=True)
            return await _next_step(db, session)
    asked = scripts.DEPARTMENT_ASK_NO_NAME if name_known else scripts.NAME_ASK
    result = await nlu.interpret_name(utterance, asked=asked)

    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    department = result.extras.get("department")
    if result.value and not name_known:
        _set_name(session, result.value, model_confidence=result.extras.get("name_confidence"))
    # The answer to "which department?" is taken as said -- unless the department is only part of a system's name
    # ("the billing application"): that is never the caller's department, so none is recorded.
    if department and not _only_a_system_name(utterance, department):
        _set_department(session, department)
    if not session.collected.get("caller_name"):
        # The name is required; the department alone is not enough.
        return await _handle_failure(db, session)
    return await _next_step(db, session)


# --- directory identity proposal ----------------------------------------------------------
#
# A directory match (by caller ID, or by an identifier the caller gives) is only ever a
# proposal: "Am I speaking with <name>?". A spoken yes marks that name as caller-confirmed.
# That is not authentication, and nothing here sets an authenticated/authorized flag. All of
# it is rule-based (no model call), and everything it knows is in this call's own `collected`.


def _pending_identity_name(session: VoiceCallSession) -> str | None:
    """The name to propose now, if there is one: a live directory entry with a sayable name that
    the caller has not rejected or confirmed, and the call is still within its proposal budget."""
    c = session.collected
    directory = c.get("directory") or {}
    name = directory.get("proposed_name")
    if not name or directory.get("rejected") or c.get("identity_confirmed"):
        return None
    if c.get("identity_rounds", 0) >= MAX_IDENTITY_ROUNDS:
        return None
    return name


def _ask_identity(session: VoiceCallSession, name: str, *, acknowledgement: str | None = None) -> TurnOutcome:
    update_collected(session, identity_rounds=session.collected.get("identity_rounds", 0) + 1, identity_unclear=0)
    session.state = VoiceCallState.CONFIRM_IDENTITY
    prompt = scripts.IDENTITY_CONFIRM.format(name=name)
    return TurnOutcome(_speak(session, _ask(session, f"{acknowledgement} {prompt}" if acknowledgement else prompt)))


def _spoken_digits(utterance: str) -> str:
    """The digits in an utterance, as digits or digit words ("double zero" is "00")."""
    digits: list[str] = []
    repeat = 1
    for token in re.findall(r"[a-z]+|\d", (utterance or "").lower()):
        if token in ("double", "triple"):
            repeat = 2 if token == "double" else 3
            continue
        digit = token if token.isdigit() else nlu._DIGIT_WORDS.get(token)
        if digit is not None:
            digits.extend([digit] * repeat)
        repeat = 1
    return "".join(digits)


def _identifier_in(utterance: str) -> str | None:
    """An email address, a ten-digit phone number or an extension (3-6 digits) the caller gave
    while answering the identity question; None if they only said yes, no or something else."""
    text = re.sub(r"^\W*no\b[\s,.]*", "", utterance or "", flags=re.IGNORECASE)
    email = nlu.quick_email(text)
    if email:
        return email
    phone = nlu.quick_phone(text)
    if phone:
        return phone
    digits = _spoken_digits(text)
    return digits if 3 <= len(digits) <= 6 else None


def _discard_proposal(session: VoiceCallSession) -> None:
    """The caller said no (or never said yes): forget the proposed identity entirely."""
    status = (session.collected.get("directory") or {}).get("status", "matched")
    update_collected(session, directory={"status": status, "rejected": True}, identity_unclear=0)


async def _handle_identity_confirmation(
    db: AsyncSession, session: VoiceCallSession, utterance: str, directory: UserDirectoryService | None
) -> TurnOutcome:
    if nlu.mentions_escalation(utterance):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    identifier = _identifier_in(utterance)
    if identifier is not None:
        return await _identity_from_identifier(db, session, identifier, directory)

    verdict = nlu.quick_yes_no(utterance, strict=True)
    if verdict is None and _rejects_proposal(utterance):
        verdict = False
    if verdict is True:
        return await _identity_confirmed(db, session)
    if verdict is False:
        rejected = (session.collected.get("directory") or {}).get("proposed_name")
        _discard_proposal(session)
        result = await _name_from_rejection(session, utterance, rejected)
        if _wants_human(utterance, result):
            return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
        return await _next_step(db, session, acknowledgement=scripts.IDENTITY_DECLINED)
    return await _identity_unclear(db, session)


#: "Not me", "wrong person", "this isn't ...", "I'm not ...": the caller turning the proposed name down.
_NOT_THEM = re.compile(
    r"\bnot me\b|\bwrong (?:person|name|number)\b|\b(?:i'?m|i am|this is|it'?s|that'?s) not\b|\b(?:this|that|it) isn'?t\b",
    re.IGNORECASE,
)


def _rejects_proposal(utterance: str) -> bool:
    """A no that is more than a bare no: "No, actually this is Rakesh", "Nope, it's not me, I'm Rakesh".

    Turning a proposal down is always safe -- nothing is recorded from the directory -- so a no is a no however
    long it is or whatever follows it. Only a yes has to be short and plain (quick_yes_no strict). Before this, a
    long no was "unclear" and the rejected name was proposed again, where a short "Yes, Rakesh here" would have
    confirmed it."""
    words = re.findall(r"[a-z']+", (utterance or "").lower())
    if not words or words[0] in nlu._YES:
        return False
    return words[0] in nlu._NO or words[0] == "not" or bool(_NOT_THEM.search(utterance))


#: The caller's own name said with the no: "No, this is Rakesh", "Nope, I'm Rakesh from IT".
_NAME_CUE = re.compile(r"\b(?:this is|i'?m|i am|my name|name is|it'?s|speaking)\b", re.IGNORECASE)


def _same_person_name(name: str, other: str | None) -> bool:
    """Does `name` share a part with `other` (allowing a recognizer's one-letter slip)?"""
    theirs = names.parts(other)
    return any(
        part == mine or (len(mine) >= 4 and nlu._edit_distance(part, mine) <= 1)
        for mine in names.parts(name) for part in theirs
    )


async def _name_from_rejection(
    session: VoiceCallSession, utterance: str, rejected: str | None
) -> nlu.TurnResult:
    """Take the caller's own name (and department) from a no that carries it, so they are not asked again. Never
    the name they just turned down: a model reading of "No, I'm not Chandu" that comes back as Chandu is dropped,
    and the rejected name is not sent to the model at all. Returns the model's result (empty when not asked)."""
    if not _NAME_CUE.search(utterance or ""):
        return nlu.TurnResult()
    result = await nlu.interpret_name(utterance, asked="Am I speaking with the person listed in our directory for this phone number?")
    if result.value and not _same_person_name(result.value, rejected):
        _set_name(session, result.value, model_confidence=result.extras.get("name_confidence"))
        department = result.extras.get("department")
        if department and not _only_a_system_name(utterance, department):
            _set_department(session, department)
    return result


async def _identity_confirmed(db: AsyncSession, session: VoiceCallSession) -> TurnOutcome:
    directory = session.collected.get("directory") or {}
    name, entry = directory.get("proposed_name"), directory.get("entry") or {}
    update_collected(
        session,
        caller_name=name,
        name_confidence="high",
        name_confirm_pending=False,
        name_confirmed_as=name,
        identity_confirmed=True,
        identity_assurance="caller_confirmed_directory_match",  # never "authenticated"
        directory_identity=entry,
        identity_unclear=0,
    )
    return await _next_step(db, session)


async def _identity_unclear(db: AsyncSession, session: VoiceCallSession) -> TurnOutcome:
    """Neither yes nor no (or silence): ask once more, then stop asking and treat it as not confirmed."""
    unclear = session.collected.get("identity_unclear", 0) + 1
    name = _pending_identity_name(session) or (session.collected.get("directory") or {}).get("proposed_name")
    if unclear > MAX_IDENTITY_UNCLEAR or not name:
        _discard_proposal(session)
        return await _next_step(db, session, acknowledgement=scripts.IDENTITY_NOT_CONFIRMED)
    update_collected(session, identity_unclear=unclear)
    session.state = VoiceCallState.CONFIRM_IDENTITY
    return TurnOutcome(_speak(session, _ask(session, scripts.IDENTITY_REASK.format(name=name))))


async def _identity_from_identifier(
    db: AsyncSession, session: VoiceCallSession, identifier: str, directory: UserDirectoryService | None
) -> TurnOutcome:
    """The caller gave another identifier instead of answering. One more lookup, and the result
    is itself only a proposal, subject to the same rules and the same budget."""
    collected = session.collected
    current = ((collected.get("directory") or {}).get("entry") or {}).get("source_row")
    result = directory.find_by_identifier(identifier) if directory is not None else None
    found = directory_identity.compact_lookup(result) if result is not None else None
    sayable = (
        found is not None
        and found.get("proposed_name")
        and found["entry"]["source_row"] != current
        and collected.get("identity_rounds", 0) < MAX_IDENTITY_ROUNDS
    )
    if sayable:
        update_collected(session, directory=found)
        return _ask_identity(session, found["proposed_name"], acknowledgement=scripts.IDENTITY_DECLINED)
    # Not found, ambiguous, a shared line, the same entry again, or no directory: ask who is calling.
    _discard_proposal(session)
    return await _next_step(db, session, acknowledgement=scripts.IDENTITY_DECLINED)


# --- name confirmation -----------------------------------------------------------


async def _handle_name_confirmation(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    """The name was read back spelled because we were unsure of it.

    Yes -> carry on. A correction -> apply it, then read the *corrected* name
    back too (never assume it), up to MAX_NAME_ROUNDS times, after which the
    caller's own spelling is accepted and noted as unverified. Corrections are
    decoded letter by letter (nlu.decode_spelled) rather than by a model that
    would "fix" an unfamiliar name back into a familiar one. Never counts as
    a misunderstanding.
    """
    if nlu.mentions_escalation(utterance) and not nlu.looks_spelled(utterance):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    collected = session.collected
    step = collected.get("name_spell_step")
    if step:
        return await _take_spelled_name_part(db, session, utterance, step)
    if collected.get("name_reconfirm"):
        return await _handle_name_reconfirmation(db, session, utterance)
    if _affirms_name(collected["caller_name"], utterance):
        return await _name_confirmed(db, session)

    if nlu.looks_spelled(utterance):
        # "No, it's Y A S H W A N T H" -- corrected in the same breath, letter by letter: taken as spelled.
        spelled = nlu.decode_spelled(utterance)
        if spelled:
            return await _accept_spelled_name(db, session, _replace_closest_part(collected["caller_name"], spelled))

    verdict = nlu.quick_yes_no(utterance, strict=True)
    if verdict is True:
        return await _name_confirmed(db, session)
    if verdict is False:
        return _ask_to_spell(session)

    # More than a plain yes/no. It may be an explicit edit ("add an H at the
    # end") -- apply it only if it is unambiguous, otherwise ask for spelling.
    fix = await nlu.interpret_name_correction(collected["caller_name"], utterance)
    if _wants_human(utterance, fix):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    if fix.value and fix.value.lower() != collected["caller_name"].lower() and names.plausible(fix.value):
        return _reconfirm_name(session, fix.value)
    answer = await nlu.interpret_yes_no("Is that name right?", utterance)
    if answer.yes_no is True:
        return await _name_confirmed(db, session)
    return _ask_to_spell(session)


async def _handle_name_reconfirmation(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    """The corrected name was read back. Yes -> done. No again -> another try, then accept."""
    if _affirms_name(session.collected["caller_name"], utterance):
        return await _name_confirmed(db, session)
    if nlu.looks_spelled(utterance):
        spelled = nlu.decode_spelled(utterance)
        if spelled:
            return await _accept_spelled_name(db, session, _replace_closest_part(session.collected["caller_name"], spelled))
    verdict = nlu.quick_yes_no(utterance, strict=True)
    if verdict is None:
        verdict = (await nlu.interpret_yes_no("Is that name right?", utterance)).yes_no
    if verdict is True:
        return await _name_confirmed(db, session)
    if session.collected.get("name_rounds", 0) >= MAX_NAME_ROUNDS:
        update_collected(session, name_unverified=True, name_reconfirm=False, name_confirm_pending=False)
        return await _next_step(db, session, acknowledgement=scripts.NAME_ACCEPT_AS_SPOKEN)
    update_collected(session, name_reconfirm=False)
    return _ask_to_spell(session)


#: A yes that is really a change: "yes, but...", "yes, just add an H", "yes, with a double S".
_NAME_EDIT = re.compile(
    r"\b(?:add|remove|delete|change|missing|extra|replace|instead|without|with an?|double|one more|another)\b",
    re.IGNORECASE,
)


def _affirms_name(name: str, utterance: str) -> bool:
    """A yes to the spelled read-back, said with more than "yes": "Yes, that's correct. My name is Rakesh.",
    "Yeah, that's right, Rakesh, R A K E S H."

    The same rules as a plain yes, applied to a longer answer: it must open with a yes, and carry no no, no "but"
    and no edit instruction (those still go to the correction steps). Any spelling in it must be the name that was
    read back. A name the caller only says aloud again is not a correction: the read-back was spelled because the
    spoken form is the unreliable one, so a recognizer's "Rakash" in "yes, Rakash" never replaces "R A K E S H"."""
    text = (utterance or "").lower()
    words = re.findall(r"[a-z']+", text)
    if not words:
        return False
    opens_with_yes = words[0] in nlu._YES or (
        words[0] in ("that's", "thats", "that", "it's", "its") and any(w in nlu._YES for w in words[1:3])
    )
    if not opens_with_yes or nlu.mentions_escalation(text):
        return False
    if any(w in nlu._NO or w == "not" for w in words) or any(p in text for p in nlu._NEGATED_YES):
        return False
    if (nlu._CONTRAST - {"just"}).intersection(words) or _NAME_EDIT.search(text):
        return False
    current = names.parts(name)
    spelled_answer = nlu.looks_spelled(utterance)
    if spelled_answer:
        spelled = (nlu.decode_spelled(utterance) or "").lower()
        if not spelled or (spelled != "".join(current) and spelled not in current):
            return False
    # Anything else must be the name said again. A word that is neither ("Yes, it's Rakesh Kumar") may add to the
    # name: the existing correction step reads it, and reads the result back.
    for word in words:
        if word in _AFFIRM_WORDS or (spelled_answer and (len(word) == 1 or word in nlu._LETTER_NAMES)):
            continue
        if not any(nlu._same_spoken_word(word, part) for part in current):
            return False
    return True


#: Words a yes to the name read-back may carry besides the name itself.
_AFFIRM_WORDS = frozenset(nlu._YES | {
    "that's", "thats", "that", "it's", "its", "it", "is", "was", "my", "name", "the", "spelling", "spelled", "spell",
    "perfect", "good", "fine", "great", "ok", "okay", "i", "i'm", "im", "am", "this", "speaking", "so", "um", "uh",
    "you", "got", "thank", "thanks", "sir", "madam", "ma'am", "all", "totally", "completely", "and", "too", "as",
    "well", "letters", "spot", "on", "there", "just", "only",
})


# --- the caller correcting their name later in the call ----------------------------------------------------------
#
# "My name is Yashwanth", "change my name", "you got my name wrong" -- at the diagnostic questions, the extension, the
# email, the read-back. Seen on a test call: corrections like these were read as answers to whatever was open. They
# are a name correction wherever they come: a spelled name is taken as spelled, a spoken one is read back spelled
# (never trusted as heard), and with no name in it the caller is asked to spell it. Then the call resumes.

_NAME_CHANGE_STATES = (
    VoiceCallState.COLLECT_DIAGNOSTICS,
    VoiceCallState.CONFIRM_NAME,
    VoiceCallState.COLLECT_PHONE,
    VoiceCallState.CONFIRM_CALLBACK_NUMBER,
    VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER,
    VoiceCallState.COLLECT_EXTENSION,
    VoiceCallState.COLLECT_EMAIL,
    VoiceCallState.CONFIRM_EMAIL,
    VoiceCallState.CONFIRM_SUMMARY,
)
#: "my name is Yashwanth", "change my name to Yashwanth": the name that follows.
_NEW_NAME = re.compile(
    r"\b(?:my name(?:'s| is)|change (?:my|the) name to)\s+(?:actually\s+|really\s+)?"
    r"(?P<name>[a-z][a-z'-]*(?:\s+[a-z][a-z'-]*)?)",
    re.IGNORECASE,
)
#: Asking for the name to be changed, with or without the new one.
_CHANGE_NAME = re.compile(
    r"\b(?:change|correct|fix|update)\s+(?:my|the)\s+name\b"
    r"|\b(?:got|have|has|spelled|wrote|heard|took)\s+my name\s+wrong\b"
    r"|\bmy name is (?:wrong|not right|incorrect|spelled wrong|misspelled)\b|\bthat'?s not my name\b|\bwrong name\b",
    re.IGNORECASE,
)
_NOT_PART_OF_NAME = {"not", "and", "but", "please", "thanks", "thank", "spelled", "so", "is", "it's", "its", "the",
                     "actually", "okay", "ok", "yes", "no", "sir", "madam"}


def _stated_name(utterance: str) -> str | None:
    """The name in "my name is Yashwanth (not ...)", capitalized; None when there is none that could be a name."""
    match = _NEW_NAME.search(utterance or "")
    if not match:
        return None
    words = []
    for word in match.group("name").split():
        if word.lower() in _NOT_PART_OF_NAME:
            break
        words.append(word.capitalize())
    name = " ".join(words)
    return name if names.plausible(name) else None


async def _maybe_name_change(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome | None:
    c = session.collected
    if (
        not utterance
        or session.state not in _NAME_CHANGE_STATES
        or not c.get("caller_name")
        or _is_patient_call(session)
        or issue_filed(session)
        or c.get("name_spell_step")  # the caller is spelling it already: that step reads it
        or _HAS_ADDRESS.search(utterance)  # "my name is ... and my email is ...": the email question's answer
    ):
        return None
    stated = _stated_name(utterance)
    current = names.parts(c["caller_name"])
    if stated and all(w in current for w in names.parts(stated)):
        # The name they already have, said again: not a correction. Exact only -- one letter different
        # ("Yashwant" for "Yashwanth") is exactly the correction a caller makes.
        stated = None
    if not stated and not _CHANGE_NAME.search(utterance):
        return None
    _reopen_interrupted(session)
    update_collected(session, name_confirmed_as=None, name_confirm_pending=False, name_spell_first=False)
    if nlu.looks_spelled(utterance):
        spelled = nlu.decode_spelled(utterance)
        if spelled and names.plausible(spelled):
            return await _accept_spelled_name(db, session, _replace_closest_part(c["caller_name"], spelled))
    if stated:
        update_collected(session, name_rounds=0)
        return _reconfirm_name(session, _replace_closest_part(c["caller_name"], stated) if len(stated.split()) == 1
                               else stated)
    return _ask_to_spell(session)


def _reopen_interrupted(session: VoiceCallSession) -> None:
    """Make sure the question the name correction interrupted is asked again once the name is settled. Questions
    asked only once (extension, email) are un-marked; an address still being read back is dropped (never used
    unconfirmed) and asked for again. The others -- a diagnostic question, the callback number, the read-back --
    come back by themselves, because they are still unanswered."""
    c = session.collected
    asked = list(c.get("asked") or [])
    if session.state == VoiceCallState.COLLECT_EXTENSION and not c.get("extension_source"):
        asked = [a for a in asked if a != "extension"]
    if session.state in (VoiceCallState.COLLECT_EMAIL, VoiceCallState.CONFIRM_EMAIL) and not c.get("email_confirmed"):
        asked = [a for a in asked if a != "email"]
        update_collected(session, email=None, email_fix=None, email_candidate=None, email_source=None)
    update_collected(session, asked=asked)


_HAS_ADDRESS = re.compile(r"@|\bat\b.*\bdot\b", re.IGNORECASE)


def _ask_to_spell(session: VoiceCallSession) -> TurnOutcome:
    update_collected(session, name_spell_step="first", name_reconfirm=False)
    session.state = VoiceCallState.CONFIRM_NAME
    return TurnOutcome(_speak(session, _ask(session, scripts.NAME_SPELL_FIRST)))


async def _accept_spelled_name(db: AsyncSession, session: VoiceCallSession, name: str) -> TurnOutcome:
    """The caller spelled it letter by letter: that is the name. Not read back and asked about again -- a
    spelled name is already the caller's own confirmation (and a silence after "is that right?" lost it before)."""
    update_collected(
        session,
        caller_name=name,
        name_confidence="high",
        name_confirmed_as=name,
        name_spelled_by_caller=True,
        name_confirm_pending=False,
        name_reconfirm=False,
        name_spell_step=None,
        name_rounds=0,
    )
    return await _next_step(db, session, acknowledgement=scripts.NAME_SPELLING_NOTED)


def _reconfirm_name(session: VoiceCallSession, name: str) -> TurnOutcome:
    """Apply a corrected name and read it back before trusting it."""
    rounds = session.collected.get("name_rounds", 0) + 1
    update_collected(
        session,
        caller_name=name,
        name_confidence="high",
        name_spell_step=None,
        name_reconfirm=True,
        name_rounds=rounds,
    )
    session.state = VoiceCallState.CONFIRM_NAME
    prompt = scripts.NAME_RECONFIRM.format(spelled=scripts.spelled_name(name))
    return TurnOutcome(_speak(session, _ask(session, prompt)))


async def _name_confirmed(db: AsyncSession, session: VoiceCallSession) -> TurnOutcome:
    name = session.collected.get("caller_name") or ""
    update_collected(
        session,
        name_confirmed_as=name,
        name_confirm_pending=False,
        name_reconfirm=False,
        name_spell_step=None,
        name_rounds=0,
    )
    return await _next_step(db, session)


async def _take_spelled_name_part(
    db: AsyncSession, session: VoiceCallSession, utterance: str, step: str
) -> TurnOutcome:
    spelled = nlu.decode_spelled(utterance) or _single_word(utterance)
    parts = (session.collected.get("caller_name") or "").split()
    if step == "first":
        if spelled:
            parts = [spelled, *parts[1:]]
        if len(parts) > 1:
            update_collected(session, caller_name=" ".join(parts), name_spell_step="last")
            return TurnOutcome(_speak(session, _ask(session, scripts.NAME_SPELL_LAST)))
    elif spelled:
        parts = [*parts[:1], spelled]
    return await _accept_spelled_name(db, session, " ".join(parts))


def _single_word(utterance: str) -> str | None:
    """The transcriber joined the spelled letters into one word ("Yashwanth.")."""
    words = [w for w in re.findall(r"[A-Za-z]+", utterance or "") if w.lower() not in ("it", "its", "s", "is", "my", "name")]
    return words[0].capitalize() if len(words) == 1 and len(words[0]) > 1 else None


def _replace_closest_part(name: str, spelled: str) -> str:
    """Put a spelled correction in place of the part of the name it fixes."""
    parts = (name or "").split()
    if len(parts) <= 1:
        return spelled
    closest = min(range(len(parts)), key=lambda i: nlu._edit_distance(parts[i].lower(), spelled.lower()))
    parts[closest] = spelled
    return " ".join(parts)


# --- phone ------------------------------------------------------------------------


async def _handle_phone(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    result = await nlu.interpret_phone(utterance)

    if session.collected.get("escalation_pending"):
        # Asked only so the promised callback has a number: take it if we
        # heard one, then hand off either way -- never re-ask here.
        if not result.failed:
            update_collected(session, phone_number=result.value, phone_spoken=True)
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    if result.failed:
        return await _phone_not_understood(db, session, utterance)

    update_collected(session, phone_number=result.value, phone_spoken=True, phone_attempts=0)
    return await _next_step(db, session)


async def _phone_not_understood(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    """A number we couldn't get. Says what was wrong; after two tries carries on.

    Seen in a real call: "11006", "1-0-0-6", then twelve digits, each answered
    with the same "one digit at a time", ending in an escalation to a person
    even though everything else had been understood. A callback number is
    valuable but not worth losing the ticket over, and it must not count
    toward escalation.
    """
    attempts = session.collected.get("phone_attempts", 0) + 1
    update_collected(session, phone_attempts=attempts)
    if attempts >= settings.voice_max_phone_attempts:
        update_collected(session, phone_skipped=True)
        return await _next_step(db, session, acknowledgement=scripts.PHONE_GIVE_UP)
    session.state = VoiceCallState.COLLECT_PHONE
    prompt = scripts.phone_retry(nlu.count_digits(utterance), attempts)
    return TurnOutcome(_speak(session, _ask(session, prompt)))


# --- callback number confirmation ---------------------------------------------------
#
# The IT team calls whatever number is on the ticket, so the caller hears it once
# before the ticket is filed. `collected` keeps three things apart:
#   phone_number        the number we had: caller ID, or what the caller said
#   callback_candidate  a different number the caller offered, awaiting a yes
#   callback_number     the number the caller confirmed (None if they never did)
# Whatever happens, the call carries on: after `voice_max_phone_attempts` failed
# tries, or a refusal, the number we already had stands.


def _callback_number_pending(session: VoiceCallSession) -> bool:
    collected = session.collected
    confirm_on = settings.voice_confirm_callback or _is_patient_call(session)
    return bool(
        confirm_on
        and collected.get("phone_number")
        and not collected.get("callback_confirmed")
    )


def _ask_callback_confirmation(session: VoiceCallSession, *, preamble: str | None = None) -> TurnOutcome:
    """Read the number back; if the caller offered another, confirm that one instead."""
    collected = session.collected
    session.state = VoiceCallState.CONFIRM_CALLBACK_NUMBER
    if collected.get("callback_candidate"):
        prompt = scripts.CALLBACK_CONFIRM_NEW.format(
            number=scripts.spoken_phone_number(collected["callback_candidate"])
        )
    else:
        # The caller type was settled earlier and is only read here. Anything but a confirmed patient
        # (including a call with no type) gets the employee wording, as every other step does.
        template = scripts.PATIENT_CALLBACK_CONFIRM if _is_patient_call(session) else scripts.CALLBACK_CONFIRM
        prompt = template.format(number=scripts.spoken_phone_number(collected["phone_number"]))
    body = _say_then_ask(session, preamble, prompt) if preamble else _ask(session, prompt)
    return TurnOutcome(_speak(session, body))


def _ask_alternate_callback(session: VoiceCallSession) -> TurnOutcome:
    session.state = VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER
    # Worded like the confirmation that led here: the caller type is only read, never set.
    prompt = scripts.PATIENT_CALLBACK_ASK_OTHER if _is_patient_call(session) else scripts.CALLBACK_ASK_OTHER
    return TurnOutcome(_speak(session, _ask(session, prompt)))


async def _callback_settled(
    db: AsyncSession, session: VoiceCallSession, number: str | None, *, preamble: str | None = None
) -> TurnOutcome:
    """Done asking. `number` is what the caller confirmed; None means they never did."""
    update_collected(
        session,
        callback_confirmed=True,
        callback_number=number,
        callback_fallback=number is None,
        callback_candidate=None,
        callback_attempts=0,
    )
    return await _finish_collection(db, session, preamble=preamble)


async def _keep_current_callback(db: AsyncSession, session: VoiceCallSession) -> TurnOutcome:
    """The caller will not (or cannot) give another number: the one we had stands."""
    line = scripts.CALLBACK_KEEP_CURRENT.format(
        number=scripts.spoken_phone_number(session.collected["phone_number"])
    )
    return await _callback_settled(db, session, None, preamble=line)


def _callback_attempts_used(session: VoiceCallSession) -> bool:
    """Count one more failed try; True once the limit is reached."""
    attempts = session.collected.get("callback_attempts", 0) + 1
    update_collected(session, callback_attempts=attempts)
    return attempts >= settings.voice_max_phone_attempts


def _propose_callback(session: VoiceCallSession, number: str) -> TurnOutcome:
    update_collected(session, callback_candidate=number)
    return _ask_callback_confirmation(session)


async def _email_still_being_given(
    db: AsyncSession, session: VoiceCallSession, utterance: str
) -> TurnOutcome | None:
    """An email fragment heard at the callback-number question, while the address was never settled.

    Seen on a call: email was dropped after "you're not listening", and the caller's "A dot S-A-I-Y" was then
    answered with "I didn't catch a yes or a no". If the agent (not the caller) left the email unresolved, an answer
    that is plainly part of an address goes back to the email, adding to what was already said -- once per call. A
    caller who chose to skip it is never asked again, and a phone number or a yes/no is the callback answer."""
    c = session.collected
    if (
        _is_patient_call(session)
        or "email" not in (c.get("asked") or [])
        or c.get("email_confirmed")
        or c.get("email_skipped") == "caller"
        or c.get("email_reopened")
        or nlu.quick_yes_no(utterance) is not None
        or nlu.count_digits(utterance) >= 7
    ):
        return None
    fragment = nlu.read_email_fragment(utterance)
    if not fragment.has_content or not (nlu.looks_spelled(utterance) or fragment.said_at or "." in (fragment.local or "")):
        return None
    update_collected(session, email_reopened=True, email_skipped=None)
    return await _email_progress(db, session, fragment, preamble=scripts.EMAIL_BACK_TO_EMAIL)


async def _handle_callback_confirmation(
    db: AsyncSession, session: VoiceCallSession, utterance: str
) -> TurnOutcome:
    collected = session.collected
    unfinished = await _email_still_being_given(db, session, utterance)
    if unfinished is not None:
        return unfinished
    result = await nlu.interpret_yes_no("Is that the best number to reach you on?", utterance)

    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    candidate = collected.get("callback_candidate")
    if result.yes_no is True:
        return await _callback_settled(db, session, candidate or collected["phone_number"])

    if result.yes_no is False:
        if nlu.count_digits(utterance) >= 7:
            # "No, it's 845 555 0142": the new number came with the no.
            offered = await nlu.interpret_phone(utterance)
            if not offered.failed:
                if offered.value == collected["phone_number"]:
                    return await _callback_settled(db, session, offered.value)
                return _propose_callback(session, offered.value)
        if candidate:
            # The caller turned down the number they offered, too.
            update_collected(session, callback_candidate=None)
            if _callback_attempts_used(session):
                return await _keep_current_callback(db, session)
        return _ask_alternate_callback(session)

    # Neither a yes nor a no. Ask once more, then carry on with the number we have.
    if _callback_attempts_used(session):
        return await _keep_current_callback(db, session)
    return _ask_callback_confirmation(session, preamble=scripts.CALLBACK_REPEAT)


async def _handle_alternate_callback(
    db: AsyncSession, session: VoiceCallSession, utterance: str
) -> TurnOutcome:
    collected = session.collected
    if nlu.phone_declined(utterance) and not nlu.mentions_escalation(utterance):
        return await _keep_current_callback(db, session)

    result = await nlu.interpret_phone(utterance)
    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    if result.failed:
        # Not a usable number: say what was wrong, ask again, give up after the limit.
        if _callback_attempts_used(session):
            return await _keep_current_callback(db, session)
        session.state = VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER
        prompt = scripts.phone_retry(nlu.count_digits(utterance), collected.get("callback_attempts", 1))
        return TurnOutcome(_speak(session, _ask(session, prompt)))

    if result.value == collected["phone_number"]:
        return await _callback_settled(db, session, result.value)  # "no, the same one"
    return _propose_callback(session, result.value)


# --- choosing the next question -------------------------------------------------


def _asked(session: VoiceCallSession, what: str) -> bool:
    return what in (session.collected.get("asked") or [])


def _mark_asked(session: VoiceCallSession, what: str) -> None:
    update_collected(session, asked=[*(session.collected.get("asked") or []), what])


# --- clarification questions (strict extraction) ----------------------------------------------

#: Systems several people share: if one is down, others probably are too, so scope is worth asking.
_SHARED_CATEGORIES = {"Network", "Printer", "eClinicalWorks"}
#: A fact is asked about at most this many times; after that it stays unknown.
MAX_ASKS_PER_FACT = 2

_CLARIFY_FIELD = {"blocked": facts_mod.WORK_BLOCKED, "patient_care": facts_mod.PATIENT_CARE, "scope": facts_mod.SCOPE}


def _mark_clarify_ask(session: VoiceCallSession, kind: str, *, counts_toward_cap: bool = True) -> None:
    asks = dict(session.collected.get("clarify_asks") or {})
    asks[kind] = asks.get(kind, 0) + 1
    update_collected(session, clarify_asks=asks)
    if counts_toward_cap:
        update_collected(session, clarify_turns=session.collected.get("clarify_turns", 0) + 1)


def _impact_decided(collected: dict) -> bool:
    """Strict extraction: patients blocked, or the whole site down, already fix the priority,
    so how the caller's own work is going is not worth a question."""
    if not settings.voice_strict_extraction:
        return False
    return (
        facts_mod.get_fact(collected, facts_mod.PATIENT_CARE).value is True
        or facts_mod.get_fact(collected, facts_mod.SCOPE).value == "whole_site"
    )


def _blocked_needed(collected: dict) -> bool:
    return collected.get("work_blocked") is None and not _impact_decided(collected)


def _next_clarification(session: VoiceCallSession) -> str | None:
    """Which impact fact, if any, to ask about next. Never loops: every ask is counted,
    each fact is asked at most MAX_ASKS_PER_FACT times, and the whole call at most
    `voice_max_clarification_turns` times."""
    if not settings.voice_strict_extraction:
        return None
    c = session.collected
    if c.get("clarify_turns", 0) >= settings.voice_max_clarification_turns:
        return None
    asks = c.get("clarify_asks") or {}
    blocked, patient, scope = (facts_mod.get_fact(c, field) for field in facts_mod.FIELDS)

    if not blocked.is_known and _blocked_needed(c) and asks.get("blocked", 0) < MAX_ASKS_PER_FACT:
        return "blocked"
    patient_matters = bool(c.get("patient_context_mentioned")) or (
        blocked.value is True and c.get("category") == "eClinicalWorks"
    )
    if not patient.is_known and patient_matters and asks.get("patient_care", 0) < MAX_ASKS_PER_FACT:
        return "patient_care"
    scope_matters = blocked.value is True or c.get("category") in _SHARED_CATEGORIES
    if not scope.is_known and scope_matters and asks.get("scope", 0) < MAX_ASKS_PER_FACT:
        return "scope"
    return None


def _clarification_prompt(session: VoiceCallSession, kind: str) -> str:
    options = scripts.CLARIFY_OPTIONS[kind]
    attempt = (session.collected.get("clarify_asks") or {}).get(kind, 1) - 1
    return options[min(attempt, len(options) - 1)]


_ANSWER_VALUE = {
    ("blocked", "yes"): True, ("blocked", "no"): False,
    ("patient_care", "yes"): True, ("patient_care", "no"): False,
    ("scope", "just_me"): "one_person", ("scope", "others"): "several_people", ("scope", "whole_site"): "whole_site",
}


def _record_clarification(session: VoiceCallSession, kind: str, answer: str, utterance: str) -> None:
    """Turn the caller's answer into a fact and close the pending question."""
    field = _CLARIFY_FIELD[kind]
    value = _ANSWER_VALUE.get((kind, answer))
    if value is not None:
        fact = facts_mod.Fact.known(
            field, value, source=facts_mod.Source.ANSWER, confidence=0.95, evidence=utterance.strip()[:200] or None
        )
    else:
        reason = facts_mod.UnknownReason.CALLER_UNSURE if answer == "unsure" else facts_mod.UnknownReason.ASKED_UNCLEAR
        fact = facts_mod.Fact.unknown(field, reason, source=facts_mod.Source.ANSWER)
        if answer == "unsure":
            # "I don't know" is a final answer: never ask this again.
            asks = dict(session.collected.get("clarify_asks") or {})
            asks[kind] = MAX_ASKS_PER_FACT
            update_collected(session, clarify_asks=asks)
    session.collected = facts_mod.apply_fact(session.collected, fact)
    update_collected(session, pending_question=None)
    _apply_priority_rules(session)


async def _handle_clarification(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    """The caller's reply to a clarification question. Optional information: an unclear
    answer never counts toward escalation, and the same question is not asked more than twice."""
    kind = session.collected["pending_question"]
    result = await nlu.interpret_clarification(kind, _clarification_prompt(session, kind), utterance)
    if _wants_human(utterance, result):
        update_collected(session, pending_question=None)
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    _record_clarification(session, kind, result.extras.get("answer", "unclear"), utterance)
    _started_from_words(session, utterance)
    _work_impact_from_words(session, utterance)
    return await _next_step(db, session)


def _details_question(session: VoiceCallSession) -> str:
    need_started = not session.collected.get("started")
    need_blocked = _blocked_needed(session.collected)
    if need_started and need_blocked:
        options = scripts.DETAILS_ASK_STRICT_OPTIONS if settings.voice_strict_extraction else scripts.DETAILS_ASK_OPTIONS
        return scripts.pick(options, len(session.turns or []))
    return scripts.DETAILS_ASK_STARTED if need_started else scripts.DETAILS_ASK_BLOCKED


def _apply_priority_rules(session: VoiceCallSession) -> None:
    """Decide the priority *and* why, from one rule (app/voice/priority.py)."""
    assessment = _assessment(session)
    # What the diagnostic questions found (a possible security incident, data loss, a failing service,
    # everyone affected) can only raise the priority, never lower it.
    floor = diagnostics.priority_floor(session.collected.get("diagnostics"))
    if floor is not None and priority_rules.order(floor[0]) > priority_rules.order(assessment.priority):
        assessment = dataclasses.replace(assessment, priority=floor[0], reason=floor[1], rule="diagnostics")
    update_collected(
        session,
        priority=assessment.priority.value,
        priority_reason=assessment.reason,
        priority_rule=assessment.rule,
        impact=assessment.impact,
    )
    if settings.voice_strict_extraction:
        update_collected(
            session,
            priority_unverified=list(assessment.unverified),
            needs_triage=assessment.needs_triage,
            priority_basis=assessment.basis,
        )


def _assessment(session: VoiceCallSession) -> priority_rules.Assessment:
    c = session.collected
    model = Priority(c.get("model_priority") or c.get("priority") or Priority.MEDIUM.value)
    if settings.voice_strict_extraction:
        # True boosts, false caps, unknown does neither -- and is reported as unverified.
        return priority_rules.assess_facts(
            model_priority=model,
            facts=facts_mod.all_facts(c),
            category=c.get("category"),
            patient_context=bool(c.get("patient_context_mentioned")),
        )
    return priority_rules.assess(
        model_priority=model,
        work_blocked=c.get("work_blocked"),
        scope=c.get("affected_scope"),
        patient_care=c.get("patient_care_affected"),
    )


async def _next_step(
    db: AsyncSession, session: VoiceCallSession, *, acknowledgement: str | None = None
) -> TurnOutcome:
    """Ask for the first thing still missing, never for something already given.

    The patient flow is shorter: name -> phone -> callback -> create ticket.
    No details, department, email, severity, clarification, or summary read-back.

    Internal IT order: when it started / can you work (asked once, only if not
    volunteered) -> name and department (department asked once) -> read back a
    doubtful name -> callback number (skipped when caller ID gave one) ->
    email (asked once) -> read the ticket back -> create it.
    """
    collected = session.collected
    patient = _is_patient_call(session)

    def ask(state: VoiceCallState, prompt: str) -> TurnOutcome:
        session.state = state
        if acknowledgement:
            prompt = f"{acknowledgement} {prompt}"
        return TurnOutcome(_speak(session, _ask(session, prompt)))

    # --- steps only for internal IT calls ---
    if not patient:
        if (not collected.get("started") or _blocked_needed(collected)) and not _asked(session, "details"):
            _mark_asked(session, "details")
            if settings.voice_strict_extraction and _blocked_needed(collected):
                _mark_clarify_ask(session, "blocked", counts_toward_cap=False)
                if collected.get("started"):
                    update_collected(session, pending_question="blocked")
                    return ask(VoiceCallState.COLLECT_DETAILS, _clarification_prompt(session, "blocked"))
            return ask(VoiceCallState.COLLECT_DETAILS, _details_question(session))

        clarification = _next_clarification(session)
        if clarification:
            _mark_clarify_ask(session, clarification)
            update_collected(session, pending_question=clarification)
            return ask(VoiceCallState.COLLECT_DETAILS, _clarification_prompt(session, clarification))

        # The issue before the paperwork: the few questions IT needs for this kind of problem come before
        # name, extension and email, so the technical details are never held up by them.
        if settings.voice_diagnostic_questions and collected.get("description"):
            prompt, notice = _diagnostics_next(session)
            if notice:
                acknowledgement = f"{acknowledgement} {notice}" if acknowledgement else notice
            if prompt:
                return ask(VoiceCallState.COLLECT_DIAGNOSTICS, prompt)

    # --- name (both flows) ---
    if not collected.get("caller_name"):
        if patient:
            return ask(VoiceCallState.COLLECT_NAME, scripts.PATIENT_NAME_ASK)
        proposed = _pending_identity_name(session)
        if proposed:
            # A directory entry matched the caller ID: ask, never assume. Employee IT calls only,
            # and only here, where the name is about to be asked for anyway.
            return _ask_identity(session, proposed, acknowledgement=acknowledgement)
        _mark_asked(session, "department")  # asked together with the name
        return ask(VoiceCallState.COLLECT_NAME, scripts.pick(scripts.NAME_ASK_OPTIONS, len(session.turns or [])))

    # --- department (IT only) ---
    if not patient and not collected.get("department") and not _asked(session, "department"):
        _mark_asked(session, "department")
        first_name = collected["caller_name"].split(" ")[0]
        return ask(VoiceCallState.COLLECT_NAME, scripts.DEPARTMENT_ASK.format(first_name=first_name))

    # --- name confirmation (IT only; patient flow keeps it simple) ---
    if not patient and settings.voice_confirm_name and collected.get("name_confirm_pending"):
        if collected.get("name_spell_first"):
            update_collected(session, name_spell_first=False, name_spell_step="first", name_reconfirm=False)
            session.state = VoiceCallState.CONFIRM_NAME
            prompt = f"{scripts.NAME_SPELL_LEAD} {scripts.NAME_SPELL_FIRST_ASK}"
            return TurnOutcome(_speak(session, _ask(session, f"{acknowledgement} {prompt}" if acknowledgement else prompt)))
        spelled = scripts.spelled_name(collected["caller_name"])
        prompt = scripts.pick(scripts.NAME_CONFIRM_OPTIONS, len(session.turns or [])).format(spelled=spelled)
        return ask(VoiceCallState.CONFIRM_NAME, prompt)

    # --- phone (both flows) ---
    if not collected.get("phone_number") and not collected.get("phone_skipped"):
        return ask(VoiceCallState.COLLECT_PHONE, scripts.PHONE_ASK)

    # --- office extension (IT only): reuse a verified one, else ask once ---
    if not patient and not collected.get("extension_source"):
        verified = _verified_extension(session)
        if verified:
            update_collected(session, extension=verified, extension_source="directory")
        elif settings.voice_ask_extension and not _asked(session, "extension"):
            _mark_asked(session, "extension")
            return ask(VoiceCallState.COLLECT_EXTENSION, scripts.EXTENSION_ASK)

    # --- email (IT only) ---
    if not patient and not collected.get("email") and not _asked(session, "email"):
        _mark_asked(session, "email")
        return _ask_for_email(session, acknowledgement=acknowledgement)

    return await _finish_collection(db, session, preamble=acknowledgement)


# --- diagnostic questions (employee calls) ------------------------------------------------------
#
# See app/voice/diagnostics.py: the question set is chosen from the problem already described, and every
# answer is read in code (no model call). Nothing is fixed or tried on the call; this only collects details.


#: The questions start straight after the problem: from its description, the when/impact details, or the
#: patient-or-employee clarification of the older flow.
_DIAGNOSTICS_START_STATES = (
    VoiceCallState.COLLECT_DESCRIPTION,
    VoiceCallState.COLLECT_DETAILS,
    VoiceCallState.CLASSIFY_CALLER_TYPE,
)


def _diagnostics_next(session: VoiceCallSession) -> tuple[str | None, str | None]:
    """(the next question, a notice to say first). (None, None) when the questions are over."""
    c = session.collected
    state = c.get("diagnostics")
    if state is None:
        if session.state not in _DIAGNOSTICS_START_STATES:
            # A call already past the problem (in progress when this code was deployed, or one that never had
            # the questions) is not sent back to them in the middle of the paperwork.
            return None, None
        state = diagnostics.begin(c.get("category"), c.get("description"), c.get("affected_scope"))
    if state.get("done"):
        return None, None
    found = diagnostics.next_prompt(state)
    if found is None:
        notice = None
        if diagnostics.urgent_reason(state) and not state.get("urgent_told"):
            notice = diagnostics.URGENT_NOTICE
            state = {**state, "urgent_told": True}
        update_collected(session, diagnostics={**state, "done": True, "pending": None})
        _apply_priority_rules(session)
        return None, notice
    key, prompt = found
    update_collected(session, diagnostics={**state, "pending": key})
    return prompt, None


async def _handle_diagnostics(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    if utterance and nlu.mentions_escalation(utterance):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    if session.collected.get("late_server"):
        return await _handle_late_server(db, session, utterance)
    state = session.collected.get("diagnostics")
    if state and state.get("asking_issue"):
        # "Sorry about that. What device or system is giving you trouble?" -- this is the answer.
        new, thing = diagnostics.named_in_answer(state, utterance)
        return await _switch_issue(db, session, new, thing, utterance, apologised=True)
    if not state or not state.get("pending"):
        return await _next_step(db, session)
    fix = diagnostics.correction(state, utterance)
    if fix is not None:
        # "You understood it wrong", "why is the printer coming up, it's just the wired keyboard": a correction to
        # the issue, never an answer to the question that was open.
        if fix["playbook"] is None:
            update_collected(session, diagnostics=diagnostics.asking_what_it_is(state))
            session.state = VoiceCallState.COLLECT_DIAGNOSTICS
            return TurnOutcome(_speak(session, _ask(session, scripts.ISSUE_WHAT_IS_IT)))
        return await _switch_issue(db, session, diagnostics.PLAYBOOKS[fix["playbook"]], fix["thing"], utterance)
    state, updates, reask = diagnostics.record(state, utterance)
    update_collected(session, diagnostics=state, **updates)
    _started_from_words(session, utterance)  # "Was it working before?" -- "Yes, until yesterday; it expired today."
    _work_impact_from_words(session, utterance)  # "Some keys aren't working, but I can still work."
    if reask:
        session.state = VoiceCallState.COLLECT_DIAGNOSTICS
        return TurnOutcome(_speak(session, _ask(session, reask)))
    if updates.get("affected_scope"):
        _apply_priority_rules(session)
    return await _next_step(db, session)


#: The category that goes with a question set, when there is one. Used to keep the ticket's category in line with a
#: corrected issue: a keyboard is not filed under Printer.
_PLAYBOOK_CATEGORY = {"printer": "Printer", "network": "Network", "outlook": "Microsoft 365", "account": "Password"}


async def _switch_issue(
    db: AsyncSession, session: VoiceCallSession, new: "diagnostics.Playbook", thing: str | None, said: str,
    *, apologised: bool = False,
) -> TurnOutcome:
    """The caller corrected what the problem is. Apologise once, say the corrected issue back, and carry on with the
    questions for it. Nothing the wrong questions recorded is kept; the correction itself goes on the ticket."""
    c = session.collected
    state = c["diagnostics"]
    old = diagnostics.PLAYBOOKS[state["playbook"]]
    fresh = diagnostics.restart(state, new, c.get("description"), said, c.get("affected_scope"))
    updates: dict = {"diagnostics": fresh}
    if thing:
        updates["short_issue"] = f"your {thing}"
        if thing.lower() not in (c.get("description") or "").lower():
            # The written problem must say what the caller says it is, not only what was first understood.
            updates["description"] = f"{c.get('description') or ''} The caller corrected this: it is their {thing}.".strip()
    category = c.get("category")
    if new.name in _PLAYBOOK_CATEGORY:
        updates.update(category=_PLAYBOOK_CATEGORY[new.name], category_confidence="high")
    elif category and category == _PLAYBOOK_CATEGORY.get(old.name):
        updates.update(category="Other", category_confidence="high")
    update_collected(session, **updates)
    _work_impact_from_words(session, said)
    session.state = VoiceCallState.COLLECT_DIAGNOSTICS
    restated = diagnostics.restatement(old, new, thing)
    if apologised:  # "Sorry about that" was said with the question: once is enough
        restated = restated.replace("Sorry about that.", "Thanks.", 1)
    prompt, notice = _diagnostics_next(session)
    if prompt:
        return TurnOutcome(_speak(session, _ask(session, f"{restated} {prompt}")))
    return await _next_step(db, session, acknowledgement=f"{restated} {notice}" if notice else restated)


# --- a server the caller remembers late ---------------------------------------------------------------------
#
# "Oh, and I have another server" -- at the extension question, the read-back, anywhere before the ticket is
# filed. Never a restart: one short question (another affected server, or a correction?), the name if it was not
# given, then back to exactly the question that was interrupted. Nothing collected earlier is lost; a correction
# keeps the old name as "corrected from", and an unclear answer adds rather than overwrites.

_LATE_ADDITION_STATES = (
    VoiceCallState.COLLECT_DIAGNOSTICS,
    VoiceCallState.CONFIRM_IDENTITY,
    VoiceCallState.COLLECT_NAME,
    VoiceCallState.CONFIRM_NAME,
    VoiceCallState.COLLECT_PHONE,
    VoiceCallState.CONFIRM_CALLBACK_NUMBER,
    VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER,
    VoiceCallState.COLLECT_EXTENSION,
    VoiceCallState.COLLECT_EMAIL,
    VoiceCallState.CONFIRM_EMAIL,
    VoiceCallState.CONFIRM_SUMMARY,
    VoiceCallState.CONFIRM_CATEGORY,
)


def _last_agent_line(session: VoiceCallSession) -> str:
    return next((t.get("text") or "" for t in reversed(session.turns or []) if t.get("role") == "agent"), "")


def _maybe_late_server(session: VoiceCallSession, utterance: str) -> TurnOutcome | None:
    c = session.collected
    state = c.get("diagnostics") or {}
    if (
        not utterance
        or state.get("playbook") not in ("server_storage", "server_access")
        or _is_patient_call(session)
        or issue_filed(session)
        or c.get("late_server")
        or session.state not in _LATE_ADDITION_STATES
        # A server question (or the "which is it?" about a second address) is already open: that answer is its own.
        or (session.state == VoiceCallState.COLLECT_DIAGNOSTICS
            and (state.get("conflict") or state.get("pending") in ("server", "server_name")))
        or not diagnostics.mentions_another_server(utterance)
    ):
        return None
    names = diagnostics.hostnames(utterance)
    host = names[0] if names else None
    existing = (state.get("values") or {}).get("server")
    back_to = (scripts.SUMMARY_REST_CORRECT if session.state == VoiceCallState.CONFIRM_SUMMARY
               else _last_agent_line(session))
    late = {"state": session.state.value, "prompt": back_to, "said": utterance, "host": host,
            "step": "kind" if existing else "name", "kind": "additional"}
    if existing and host:
        question = f"Is {host} another server that's affected as well, or should it replace {existing}?"
    elif existing:
        question = f"Is another server affected as well as {existing}, or did I get {existing} wrong?"
    else:
        question = scripts.LATE_SERVER_NAME
    update_collected(session, late_server=late)
    session.state = VoiceCallState.COLLECT_DIAGNOSTICS
    return TurnOutcome(_speak(session, _ask(session, question)))


async def _handle_late_server(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    late = dict(session.collected["late_server"])
    names = diagnostics.hostnames(utterance)
    if late["step"] == "kind":
        late["kind"] = diagnostics.server_change_kind(utterance)
        late["host"] = late.get("host") or (names[0] if names else None)
        if not late["host"]:
            late["step"] = "name"
            update_collected(session, late_server=late)
            question = scripts.LATE_SERVER_NAME if late["kind"] == "additional" else scripts.LATE_SERVER_CORRECT_NAME
            return TurnOutcome(_speak(session, _ask(session, question)))
    else:  # the name
        if names:
            late["host"] = names[0]
        elif not diagnostics.is_non_answer(utterance):
            late["host"] = diagnostics.clean(utterance)
        if utterance and not diagnostics.is_non_answer(utterance):
            late["said"] = f"{late['said']} / {utterance}"
    state = diagnostics.add_server(session.collected["diagnostics"], late.get("host"), late["said"], late["kind"])
    update_collected(session, diagnostics=state, late_server=None)
    session.state = VoiceCallState(late["state"])
    noted = scripts.LATE_SERVER_CORRECTED if late["kind"] == "correction" else scripts.LATE_SERVER_ADDED
    return TurnOutcome(_speak(session, _ask(session, f"{noted} {late['prompt']}".strip())))


# --- office extension (employee calls) ----------------------------------------------------------
#
# Asked once ("Do you have an office extension number?"), and only when no verified extension is known:
# a directory entry the caller said yes to already has one, and it is reused, never asked for or
# overwritten. Rule-based, no model call. An extension is 2-6 digits and is never taken as (or
# stored as) the callback number; a ten-digit answer is a phone number and is not kept as either.

_EXTENSION_DECLINE = re.compile(
    r"\b(?:no|nope|none|nah|skip|not sure|no idea|n/?a|"
    r"(?:i\s+)?(?:don'?t|do\s+not|haven'?t|have\s+not)\s+(?:have|got|know)|no\s+extension|there\s+isn'?t)\b",
    re.IGNORECASE,
)


def _extension_digits(utterance: str) -> str:
    """The extension the caller said, as digits. Numerals win when the transcript has any ("2001");
    otherwise digit words ("two zero zero one", "double one"). The longest run is taken, so the
    "one" in "I have one" or an opening "oh," never becomes part of the number."""
    text = utterance or ""
    numeral_runs = [re.sub(r"\D", "", run) for run in re.findall(r"\d[\d\s,.-]*\d|\d", text)]
    if numeral_runs:
        return max(numeral_runs, key=len)
    runs: list[str] = []
    current: list[str] = []
    repeat = 1
    tokens = re.findall(r"[a-z]+", text.lower())
    if tokens and tokens[0] in ("oh", "o"):
        tokens = tokens[1:]  # "Oh, ..." is an interjection, not a zero
    for token in tokens:
        if token in ("double", "triple"):
            repeat = 2 if token == "double" else 3
            continue
        digit = nlu._DIGIT_WORDS.get(token)
        if digit is None:
            if current:
                runs.append("".join(current))
            current, repeat = [], 1
            continue
        current.extend([digit] * repeat)
        repeat = 1
    if current:
        runs.append("".join(current))
    return max(runs, key=len) if runs else ""


def _verified_extension(session: VoiceCallSession) -> str | None:
    """The extension from a directory entry the caller confirmed, if it has a usable one."""
    c = session.collected
    if not c.get("identity_confirmed"):
        return None
    return normalize_extension((c.get("directory_identity") or {}).get("extension"))


async def _handle_extension(
    db: AsyncSession, session: VoiceCallSession, utterance: str, directory: UserDirectoryService | None
) -> TurnOutcome:
    if utterance and nlu.mentions_escalation(utterance):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    c = session.collected
    digits = _extension_digits(utterance)
    extension = normalize_extension(digits) if 2 <= len(digits) <= 6 else None
    if extension:
        return await _extension_settled(db, session, extension, "caller", directory)
    if utterance and _EXTENSION_DECLINE.search(utterance):
        return await _extension_settled(db, session, None, "none", directory)

    def ask_again(prompt: str) -> TurnOutcome:
        session.state = VoiceCallState.COLLECT_EXTENSION
        return TurnOutcome(_speak(session, _ask(session, prompt)))

    if len(digits) >= 7:
        # A phone number, not an extension: kept as neither. Said once, then the call moves on.
        if not c.get("extension_retried"):
            update_collected(session, extension_retried=True)
            return ask_again(scripts.EXTENSION_NOT_A_PHONE)
        return await _extension_settled(db, session, None, "unclear", directory)
    if utterance and nlu.quick_yes_no(utterance) is True and not c.get("extension_number_asked"):
        update_collected(session, extension_number_asked=True)  # "yes" without the number: ask for it once
        return ask_again(scripts.EXTENSION_WHAT)
    if not c.get("extension_retried") and not c.get("extension_number_asked"):
        update_collected(session, extension_retried=True)
        return ask_again(scripts.EXTENSION_RETRY)
    return await _extension_settled(db, session, None, "unclear", directory)


async def _extension_settled(
    db: AsyncSession, session: VoiceCallSession, extension: str | None, source: str,
    directory: UserDirectoryService | None,
) -> TurnOutcome:
    """Record the answer and move on."""
    _record_extension(session, extension, source, directory)
    if extension:
        acknowledgement = scripts.EXTENSION_NOTED.format(extension=" ".join(extension))
    else:
        acknowledgement = scripts.EXTENSION_NONE if source == "none" else None
    return await _next_step(db, session, acknowledgement=acknowledgement)


def _record_extension(
    session: VoiceCallSession, extension: str | None, source: str, directory: UserDirectoryService | None
) -> None:
    """A caller-given extension is checked against the directory only to note how it compares for the
    helpdesk; it never changes who the caller is, and another employee's name is never said or stored."""
    note = None
    c = session.collected
    # The extension the directory has for this caller ID (a confirmed identity, or the caller-ID match).
    own = normalize_extension(
        (c.get("directory_identity") or {}).get("extension")
        or (((c.get("directory") or {}).get("entry") or {}).get("extension"))
    )
    if extension and own and own != extension:
        # Kept as the caller said it, never replaced by the directory's -- and marked, so nobody takes it as checked.
        note = f"unverified: the directory entry for this caller ID lists extension {own}"
    elif extension and own == extension:
        note = "matches the directory entry for this caller ID"
    elif extension and directory is not None:
        result = directory.find_by_extension(extension)
        if result.status is LookupStatus.MATCHED and result.entry is not None:
            listed = (directory_identity.person_name(result.entry) or "").lower()
            same = listed and listed == (c.get("caller_name") or "").strip().lower()
            note = "matches the directory entry for this caller's name" if same else (
                "unverified: the directory lists this extension for a different entry"
            )
        elif result.status is LookupStatus.AMBIGUOUS:
            note = "unverified: the directory lists this extension for more than one entry"
        elif result.status is LookupStatus.NOT_FOUND:
            note = "unverified: not found in the directory"
    update_collected(session, extension=extension, extension_source=source, extension_note=note)


# --- an extension corrected after the extension question ----------------------------------------------------
#
# "Sorry, my extension is 3006" while the email question is open. Seen on a test call: the email step read every
# answer as an address, so the correction was taken for a failed email, the caller was asked to spell it again, and
# the extension stayed wrong. Like a server remembered late, the correction is recorded (and checked against the
# directory exactly as at the extension question: noted, never marked verified), said back, and the question that
# was open is asked again -- without counting as an answer to it. When the same sentence also answers that question
# ("rakesh at hfmg dot net, and my extension is 3006"), the extension clause is taken out and the rest is answered
# as usual, so neither is lost.

_EXTENSION_CORRECTION_STATES = (
    VoiceCallState.COLLECT_EMAIL,
    VoiceCallState.CONFIRM_EMAIL,
    VoiceCallState.CONFIRM_CALLBACK_NUMBER,
    VoiceCallState.CONFIRM_SUMMARY,
)
_DIGIT_TOKEN = r"(?:\d+|\b(?:zero|oh|o|one|two|three|four|five|six|seven|eight|nine|double|triple)\b)"
_DIGIT_RUN = rf"{_DIGIT_TOKEN}(?:[\s,.-]+{_DIGIT_TOKEN})*"
#: "..., not 3000": the number being corrected away from, which is not the extension.
_NOT_THIS_NUMBER = re.compile(rf"\b(?:not|instead of)\s+{_DIGIT_RUN}", re.IGNORECASE)
#: The extension clause itself: "and my extension is 3006", "ext. three zero zero six", "my office extension: 3006".
#: Only the word "extension" makes it one: digits alone at the email question may be part of the address.
_EXTENSION_CLAUSE = re.compile(
    rf"(?:\b(?:and|also|plus)\s+)?(?:\b(?:my|the|our)\s+)?(?:\boffice\s+)?\bext(?:ension)?\b\.?(?:\s+number)?"
    rf"(?:\s+(?:is|was|should be|it's|its|actually))*[\s:,-]*(?P<digits>{_DIGIT_RUN})",
    re.IGNORECASE,
)
#: What is left of a correction that says nothing else ("Oh, sorry, my extension is 3006").
_CORRECTION_FILLER = frozenset({
    "oh", "sorry", "wait", "actually", "so", "um", "uh", "and", "also", "plus", "the", "my", "it's", "its", "is", "it",
    "ok", "okay", "hold", "on", "hang", "by", "way", "btw", "oops", "just", "one", "more", "thing", "correction",
    "i", "meant", "mean", "said", "that", "was", "wrong", "earlier",
})


def _late_extension(session: VoiceCallSession, utterance: str) -> tuple[str, str] | None:
    """(the extension, everything else the caller said) for an answer that explicitly gives the extension while a
    later question is open, else None. "rakesh at hfmg dot net, and my extension is 3006" gives ("3006",
    "rakesh at hfmg dot net"): both are used."""
    if (
        not utterance
        or session.state not in _EXTENSION_CORRECTION_STATES
        or _is_patient_call(session)
        or issue_filed(session)
    ):
        return None
    text = _NOT_THIS_NUMBER.sub(" ", utterance)
    clause = _EXTENSION_CLAUSE.search(text)
    if clause is None:
        return None
    digits = _extension_digits(clause.group("digits"))
    if not 2 <= len(digits) <= 6:
        return None
    rest = f"{text[:clause.start()]} {text[clause.end():]}"
    rest = re.sub(r"^[\s,.;:!-]*(?:(?:and|also|plus)\b)?[\s,.;:!-]*", "", " ".join(rest.split()))
    return normalize_extension(digits), rest.strip(" ,.;:-")


def _extension_before_leaving(
    session: VoiceCallSession, utterance: str, directory: UserDirectoryService | None
) -> str | None:
    """An extension said in the same breath as goodbye, recorded before the call is wrapped up. Returns the
    acknowledgement to say, or None when there was none.

    The goodbye check comes first in every turn, so before this "3006, thanks, bye" at the extension question, or
    "my extension is 3006, bye" at the email question, ended the call and the extension was lost."""
    if session.state == VoiceCallState.COLLECT_EXTENSION:
        if _is_patient_call(session):
            return None
        digits = _extension_digits(utterance)  # the question asked for it: no "extension" word needed
        extension = normalize_extension(digits) if 2 <= len(digits) <= 6 else None
    else:
        found = _late_extension(session, utterance)
        extension = found[0] if found else None
    if not extension:
        return None
    _record_extension(session, extension, "caller", directory)
    return scripts.EXTENSION_NOTED.format(extension=" ".join(extension))


#: Sign-off words: what is left of "... my extension is 3006, okay, thank you so much, have a good day, bye bye".
_SIGN_OFF = frozenset({
    "thank", "thanks", "you", "much", "so", "bye", "goodbye", "byebye", "bye-bye", "have", "a", "good", "great", "nice",
    "day", "cheers", "take", "care", "that's", "all", "see", "ya", "later", "to", "say", "for", "your", "help",
})


def _only_goodbye(rest: str) -> bool:
    """Is what is left of a correction a goodbye and nothing else, however long? (nlu.is_goodbye stops at seven
    words, which a correction followed by a polite goodbye easily passes.)"""
    words = re.findall(r"[a-z'-]+", (rest or "").lower())
    return (any(w in ("bye", "goodbye", "byebye", "bye-bye") for w in words)
            and all(w in _SIGN_OFF or w in _CORRECTION_FILLER for w in words))


def _says_more(rest: str) -> bool:
    """Is there an answer to the open question left once the extension clause is taken out?"""
    return any(w not in _CORRECTION_FILLER for w in re.findall(r"[a-z0-9@']+", (rest or "").lower()))


def _lead_with(session: VoiceCallSession, outcome: TurnOutcome, line: str) -> TurnOutcome:
    """Say `line` first in this turn's reply, and keep the transcript's agent line the same as what is spoken."""
    outcome.lines = [line, *outcome.lines]
    turns = session.turns or []
    if turns and turns[-1].get("role") == "agent":
        session.turns = [*turns[:-1], {**turns[-1], "text": f"{line} {turns[-1].get('text') or ''}".strip()}]
    return outcome


def _open_question(session: VoiceCallSession) -> str:
    """The question that was open, asked again as it stands now (never a line that names the old extension)."""
    c = session.collected
    fix = c.get("email_fix") or {}
    if session.state == VoiceCallState.COLLECT_EMAIL:
        return {"local": scripts.EMAIL_FIX_LOCAL, "domain": scripts.EMAIL_FIX_DOMAIN}.get(
            fix.get("step"), scripts.EMAIL_ASK_NO_NAME)
    if session.state == VoiceCallState.CONFIRM_EMAIL:
        if fix.get("step") == "which":
            return scripts.EMAIL_WHICH_PART
        if c.get("email_source") == "directory" and c.get("email_candidate"):
            return scripts.EMAIL_DIRECTORY_CONFIRM.format(email=scripts.spoken_email(c["email_candidate"]))
        return scripts.EMAIL_CONFIRM.format(email=scripts.spoken_email(c.get("email") or ""))
    return scripts.SUMMARY_REST_CORRECT  # the read-back: the extension is not in it, so only the rest is asked


def _take_late_extension(
    session: VoiceCallSession, utterance: str, directory: UserDirectoryService | None
) -> tuple[TurnOutcome | None, str | None, str]:
    """(a reply, the acknowledgement, the utterance left for the open question).

    No extension in it: (None, None, utterance), and nothing changes. Only an extension: it is recorded and the open
    question is asked again -- (reply, None, ""). An extension and an answer ("rakesh at hfmg dot net, and my
    extension is 3006"): the extension is recorded and the rest goes to the open question as its answer, with the
    acknowledgement said first -- (None, acknowledgement, rest)."""
    found = _late_extension(session, utterance)
    if found is None:
        return None, None, utterance
    extension, rest = found
    _record_extension(session, extension, "caller", directory)
    noted = scripts.EXTENSION_NOTED.format(extension=" ".join(extension))
    if _says_more(rest):
        return None, noted, rest
    return _ask_after_extension(session, noted), None, ""


def _ask_after_extension(session: VoiceCallSession, noted: str) -> TurnOutcome:
    if session.state == VoiceCallState.CONFIRM_CALLBACK_NUMBER:
        return _ask_callback_confirmation(session, preamble=noted)
    if session.state == VoiceCallState.CONFIRM_SUMMARY:
        update_collected(session, summary_rounds=session.collected.get("summary_rounds", 0) + 1,
                         summary_awaiting_change=False)
    return TurnOutcome(_speak(session, _ask(session, f"{noted} {_open_question(session)}")))


def _extension_detail(collected: dict, confirmed: bool, who: dict) -> str | None:
    """The ticket's Extension line: what the caller said (and how it compares with the directory), else the
    confirmed directory one, else whether they have none. A caller's own answer is never hidden behind the
    directory's: when they differ, the caller's is shown with the difference."""
    source = collected.get("extension_source")
    if source == "caller" and collected.get("extension"):
        note = collected.get("extension_note")
        return f"{collected['extension']} (given by the caller{'; ' + note if note else ''})"
    if confirmed and who.get("extension"):
        return who["extension"]
    if source == "none":
        return "none (the caller has no office extension)"
    if source == "unclear":
        return "not captured (the caller's answer was unclear)"
    return None


def _department_only(utterance: str) -> departments.DepartmentMatch | None:
    """A short answer that is just a known department ("Billing.", "the IT department"): matched in
    code, so the department-only question needs no model call. Anything longer or unknown is left to
    the model, which also hears the name, corrections and requests for a person."""
    words = re.findall(r"[a-z']+", (utterance or "").lower())
    if not words or len(words) > 4 or nlu.mentions_escalation(utterance):
        return None
    match = departments.canonicalize(utterance)
    if match is None or not match.verified or _only_a_system_name(utterance, match.name):
        return None  # "the billing application" is a system, not a department: left to the model
    return match


_EMAIL_CHECK = TypeAdapter(EmailStr)


def _ticket_accepts_email(email: str | None) -> bool:
    """The ticket form rejects some addresses that are the right shape (reserved names such as .test).
    An address it cannot hold is never read back, offered or confirmed: filing the ticket would fail."""
    try:
        _EMAIL_CHECK.validate_python(email)
    except ValueError:
        return False
    return True


#: Corrections of one part of an address (the username or the domain) in one call, before the agent
#: gives up on email. Independent of the attempts for a full re-dictation (voice_max_email_attempts).
MAX_EMAIL_FIXES = 3


def _directory_email_offer(session: VoiceCallSession) -> str | None:
    """An address from the caller's confirmed directory entry that can be offered, exactly as on
    file. Offered for the caller's yes, never filled in: see directory_identity.directory_email_for."""
    c = session.collected
    if not c.get("identity_confirmed") or c.get("email"):
        return None
    offered = directory_identity.directory_email_for(c.get("caller_name") or "", (c.get("directory_identity") or {}).get("email"))
    return offered if offered and _ticket_accepts_email(offered) else None


def _ask_for_email(
    session: VoiceCallSession, *, acknowledgement: str | None = None, offer: bool = True
) -> TurnOutcome:
    offered = _directory_email_offer(session) if offer else None
    if offered:
        update_collected(session, email_candidate=offered, email_source="directory")
        session.state = VoiceCallState.CONFIRM_EMAIL
        prompt = scripts.EMAIL_DIRECTORY_CONFIRM.format(email=scripts.spoken_email(offered))
        return TurnOutcome(_speak(session, _ask(session, f"{acknowledgement} {prompt}" if acknowledgement else prompt)))
    first_name = (session.collected.get("caller_name") or "").split(" ")[0]
    prompt = (
        scripts.EMAIL_ASK.format(first_name=first_name) if first_name else scripts.EMAIL_ASK_NO_NAME
    )
    if acknowledgement:
        prompt = f"{acknowledgement} {prompt}"
    session.state = VoiceCallState.COLLECT_EMAIL
    return TurnOutcome(_speak(session, _ask(session, prompt)))


# --- collecting an email address ----------------------------------------------------------------------------------
#
# Seen on a test call: "Ranga dot S-I-S-1" was read back at once; "Actually, it's SAI dot..." restarted the spelling;
# the caller said "you're not listening", email was dropped, and "A dot S-A-I-Y" landed on the callback-number
# question. Email collection is now a small state machine inside the two email states, kept in `collected`:
#
#   collecting   email_draft = {local, domain, open}: what the caller has dictated so far, kept across turns. A turn
#                that ends mid-address ("SAI dot...") keeps it and asks them to go on; a correction ("actually,
#                it's ...") replaces it, or only the segment it fixes; a continuation is added to it.
#   read-back    a complete address is validated in code and read back once (CONFIRM_EMAIL).
#   correction   at the read-back, a "no" with an address in it edits the draft; a bare "no" asks which part.
#   confirmed    email_confirmed=True: only now can the address reach a ticket.
#   skipped      the caller said skip, or chose to after the agent offered (it always offers before giving up).
#
# "You're not listening" / "let me finish" stops the prompts: the agent apologises, waits, and the next answer is
# still read as the address. Words are read by rules; the model is only asked about a whole answer the rules could
# not read, and anything it returns is validated the same way. Email stays optional throughout.

#: The caller is frustrated, or wants to finish: stop prompting and listen.
_EMAIL_INTERRUPTED = re.compile(
    r"\b(?:you(?:'re| are)? not listening|not listening to me|let me (?:finish|speak|talk|say it)|"
    r"(?:you(?:'re| are)?|stop|keep) (?:interrupting|cutting me off)|i(?:'m| am) not (?:done|finished)|"
    r"i was(?:n'?t| not) (?:done|finished)|i hadn'?t finished|let me complete)\b",
    re.IGNORECASE,
)
#: The caller needs a moment.
_EMAIL_PAUSE = re.compile(
    r"^\W*(?:(?:hold on|hang on|wait|one (?:second|sec|moment|minute)|give me a (?:second|sec|moment|minute)|"
    r"just a (?:second|sec|moment|minute)|let me (?:think|check|see))\W*)+$",
    re.IGNORECASE,
)
#: "Try again", "once more" -- the answer to the offer to skip.
_EMAIL_TRY_AGAIN = re.compile(r"\b(?:try (?:again|once more)|once more|one more (?:time|try)|again)\b", re.IGNORECASE)


def _email_draft(session: VoiceCallSession) -> dict:
    return dict(session.collected.get("email_draft") or {})


def _read_back_email(session: VoiceCallSession, email: str, *, preamble: str | None = None) -> TurnOutcome:
    local, _, domain = email.partition("@")
    update_collected(session, email=email, email_confirmed=False,  # read back first: not the caller's yet
                     email_draft={"local": local, "domain": domain, "open": False})  # what a correction edits
    session.state = VoiceCallState.CONFIRM_EMAIL
    prompt = scripts.EMAIL_CONFIRM.format(email=scripts.spoken_email(email))
    return TurnOutcome(_speak(session, _ask(session, f"{preamble} {prompt}" if preamble else prompt)))


def _ask_email(session: VoiceCallSession, prompt: str) -> TurnOutcome:
    session.state = VoiceCallState.COLLECT_EMAIL
    return TurnOutcome(_speak(session, _ask(session, prompt)))


async def _skip_email(
    db: AsyncSession, session: VoiceCallSession, *, preamble: str | None = None, by: str = "caller"
) -> TurnOutcome:
    """Carry on without an email. `by`: "caller" (they said skip) or "agent" (it could not be understood); only the
    second may be reopened if the caller turns out to be still dictating."""
    draft = _email_draft(session)
    update_collected(session, email=None, email_confirmed=False, email_fix=None, email_skipped=by,
                     email_draft=draft if by == "agent" else {})
    return await _finish_collection(db, session, preamble=preamble)


async def _email_give_up_or_offer(db: AsyncSession, session: VoiceCallSession) -> TurnOutcome:
    """Too many unsuccessful tries: ask once whether to try again or skip -- never just move on -- and only then
    carry on without it. The draft is kept, so an answer that carries on dictating still counts."""
    update_collected(session, email=None, email_fix=None)
    if session.collected.get("email_skip_offered"):
        return await _skip_email(db, session, preamble=scripts.EMAIL_GIVE_UP, by="agent")
    update_collected(session, email_skip_offered=True)
    return _ask_email(session, scripts.EMAIL_OFFER_SKIP)


async def _email_failed(db: AsyncSession, session: VoiceCallSession, prompt: str) -> TurnOutcome:
    """An answer that could not be read. Email is optional, so this never counts toward escalation."""
    session.email_attempt_count += 1
    limit = 1 if session.collected.get("email_frustrated") else settings.voice_max_email_attempts
    if session.email_attempt_count >= limit:
        return await _email_give_up_or_offer(db, session)
    return _ask_email(session, prompt)


def _replace_closest_segment(local: str, segment: str) -> str:
    """"ranga.sis1" corrected with "sai" -> "ranga.sai": a one-segment correction replaces the segment it is
    closest to, and nothing else."""
    parts = local.split(".")
    closest = min(range(len(parts)), key=lambda i: nlu._edit_distance(parts[i], segment))
    parts[closest] = segment
    return ".".join(parts)


async def _email_progress(
    db: AsyncSession, session: VoiceCallSession, fragment: "nlu.EmailFragment", *, preamble: str | None = None
) -> TurnOutcome:
    """Add one turn of dictation to the draft, then read back a complete address, or ask for what is missing."""
    draft = _email_draft(session)
    local, domain, was_open = draft.get("local"), draft.get("domain"), bool(draft.get("open"))
    if fragment.local:
        piece = fragment.local
        if was_open and not fragment.correction and local:
            local = local + piece  # carrying on from "... sai dot"
        elif (fragment.correction and local and not fragment.incomplete and not fragment.said_at
              and "." not in piece.strip("._-") and "." in local.strip("._-")):
            local = _replace_closest_segment(local.strip("._-"), piece)  # "no, it's S A I"
        else:
            local = piece  # a new address, or a correction that starts it again
    if fragment.domain:
        domain = fragment.domain
    unfinished = fragment.incomplete or (fragment.said_at and not fragment.domain) or (bool(domain) and not local)
    awaiting = "domain" if (fragment.said_at and not domain and local) else "local" if (domain and not local) else None
    update_collected(session, email_draft={"local": local, "domain": domain, "open": unfinished, "awaiting": awaiting},
                     email=None, email_fix=None)
    lead = f"{preamble} " if preamble else ""
    if awaiting == "domain":
        return _ask_email(session, f"{lead}{scripts.EMAIL_ASK_DOMAIN}")
    if awaiting == "local":  # only a domain so far
        return _ask_email(session, f"{lead}{scripts.EMAIL_ASK_LOCAL}")
    if unfinished:
        prompt = scripts.EMAIL_CONTINUE_AGAIN if was_open else scripts.EMAIL_CONTINUE
        return _ask_email(session, f"{lead}{prompt}")
    email = nlu.normalize_email(f"{local.strip('._-')}@{domain or nlu.HOME_EMAIL_DOMAIN}")
    if email is None or not _ticket_accepts_email(email):
        update_collected(session, email_draft={})
        return await _email_failed(db, session, f"{lead}{scripts.EMAIL_RETRY}")
    return _read_back_email(session, email, preamble=preamble)


async def _handle_email(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    collected = session.collected
    fix = collected.get("email_fix")
    if fix and fix.get("step") in ("local", "domain"):
        return await _handle_email_section(db, session, utterance, fix)
    if nlu.mentions_escalation(utterance):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    if _EMAIL_INTERRUPTED.search(utterance):
        update_collected(session, email_frustrated=True)
        fragment = nlu.read_email_fragment(utterance)
        if fragment.has_content:
            return await _email_progress(db, session, fragment, preamble=scripts.EMAIL_SORRY_INTERRUPTED)
        update_collected(session, email_draft={**_email_draft(session), "open": bool(_email_draft(session).get("local"))})
        return _ask_email(session, scripts.EMAIL_LET_ME_FINISH)  # not an attempt: the next answer is still the address
    if _EMAIL_PAUSE.match(utterance):
        return _ask_email(session, scripts.EMAIL_TAKE_YOUR_TIME)

    if collected.get("email_skip_offered") and not collected.get("email_skip_answered"):
        update_collected(session, email_skip_answered=True)
        verdict = nlu.quick_yes_no(utterance)
        if nlu.email_declined(utterance) or verdict is False:
            return await _skip_email(db, session, preamble=scripts.EMAIL_SKIPPED)
        if verdict is True or _EMAIL_TRY_AGAIN.search(utterance):
            session.email_attempt_count = 0
            update_collected(session, email_draft={})
            return _ask_email(session, scripts.EMAIL_ASK_AGAIN)

    if nlu.email_declined(utterance) or utterance.strip().lower().strip(".!") in ("skip", "skip it", "no email"):
        return await _skip_email(db, session)

    awaiting = _email_draft(session).get("awaiting")
    if awaiting in ("local", "domain"):
        # "And the part after the at sign?" -- "h f m g dot net": that part, with no "at" needed.
        section = nlu.parse_email_section(utterance, awaiting)
        if section:
            fragment = nlu.EmailFragment(local=section) if awaiting == "local" else nlu.EmailFragment(domain=section)
            return await _email_progress(db, session, fragment)
    fragment = nlu.read_email_fragment(utterance, continuing=bool(_email_draft(session).get("open")))
    if fragment.has_content:
        return await _email_progress(db, session, fragment)
    if _email_draft(session).get("open"):
        return await _email_failed(db, session, scripts.EMAIL_CONTINUE_UNCLEAR)

    # Nothing the rules could read: the model may still make out a whole address. Validated like any other.
    result = await nlu.interpret_email(utterance)
    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    if result.extras.get("declined"):
        return await _skip_email(db, session)
    if result.failed or not _ticket_accepts_email(result.value):
        return await _email_failed(db, session, scripts.EMAIL_RETRY)
    session.email_attempt_count += 1
    local, _, domain = result.value.partition("@")
    update_collected(session, email_draft={"local": local, "domain": domain, "open": False})
    return _read_back_email(session, result.value)


async def _handle_email_confirmation(
    db: AsyncSession, session: VoiceCallSession, utterance: str
) -> TurnOutcome:
    collected = session.collected
    if collected.get("email_source") == "directory":
        return await _handle_directory_email_answer(db, session, utterance)
    if _EMAIL_INTERRUPTED.search(utterance) or _EMAIL_PAUSE.match(utterance):
        # Stop and listen: back to collecting, keeping what they said as the draft to edit.
        update_collected(session, email=None, email_fix=None, email_frustrated=bool(_EMAIL_INTERRUPTED.search(utterance)))
        return await _handle_email(db, session, utterance)
    fix = collected.get("email_fix")
    if fix and fix.get("step") == "which":
        return await _handle_email_which_part(db, session, utterance, fix)

    result = await nlu.interpret_yes_no("Is that email correct?", utterance)

    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    if result.yes_no is True and not _corrects_address(utterance):
        update_collected(session, email_confirmed=True)  # only now may the address reach a ticket
        return await _finish_collection(db, session)

    if _corrects_address(utterance):
        # "No, actually it's SAI dot...": an edit of the address just read back, never a restart.
        fragment = nlu.read_email_fragment(utterance)
        fragment.correction = True
        update_collected(session, email_fixes=collected.get("email_fixes", 0) + 1)
        return await _email_progress(db, session, fragment)

    # Wrong or unclear: find out which part, and ask for that part alone.
    return await _start_email_fix(db, session, utterance)


def _corrects_address(utterance: str) -> bool:
    """An answer to the read-back that carries address words of its own (not "which part")."""
    return _which_email_part(utterance) is None and nlu.read_email_fragment(utterance).has_content


# --- correcting one part of an email address ----------------------------------------------------------
#
# A wrong read-back is usually wrong in one place. The caller is asked which part (or says it in the
# same breath), and only that part is asked for again; the other part is kept exactly as confirmed
# so far. Everything is rule-based, the corrected address is read back in full, and nothing is
# changed except what the caller said.


def _which_email_part(utterance: str) -> str | None:
    """"local" (before the at sign, the username), "domain" (after it), "both", or None if not said."""
    text = " ".join((utterance or "").lower().split())
    words = set(re.findall(r"[a-z']+", text))
    local = bool(words & {"before", "first", "front", "username", "beginning"} or "user name" in text)
    domain = bool(words & {"after", "domain", "end", "ending", "second"})
    both = bool(words & {"both", "everything", "entire", "whole"} or "start over" in text or "from the top" in text)
    if both or (local and domain):
        return "both"
    return "local" if local else "domain" if domain else None


async def _retry_email_from_scratch(db: AsyncSession, session: VoiceCallSession) -> TurnOutcome:
    update_collected(session, email=None, email_fix=None, email_draft={})
    if session.email_attempt_count >= settings.voice_max_email_attempts:
        return await _email_give_up_or_offer(db, session)
    return _ask_email(session, scripts.EMAIL_RETRY)


async def _start_email_fix(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    collected = session.collected
    local, _, domain = (collected.get("email") or "").partition("@")
    update_collected(session, email=None)  # an address the caller rejected is not kept
    if not local or not domain:
        return await _retry_email_from_scratch(db, session)
    if collected.get("email_fixes", 0) >= MAX_EMAIL_FIXES:
        return await _email_give_up_or_offer(db, session)
    fix = {"local": local, "domain": domain, "tries": 0}
    part = _which_email_part(utterance)
    if part == "both":
        return await _retry_email_from_scratch(db, session)
    if part is None and nlu.looks_spelled(utterance):
        part = "local"  # "no, it's s a i": the corrected username, spelled in the same breath
    if part in ("local", "domain"):
        return await _begin_email_section(db, session, utterance, fix, part)
    update_collected(session, email_fix={**fix, "step": "which"})
    session.state = VoiceCallState.CONFIRM_EMAIL
    return TurnOutcome(_speak(session, _ask(session, scripts.EMAIL_WHICH_PART)))


async def _handle_email_which_part(
    db: AsyncSession, session: VoiceCallSession, utterance: str, fix: dict
) -> TurnOutcome:
    if nlu.mentions_escalation(utterance):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    part = _which_email_part(utterance)
    if part is None and nlu.looks_spelled(utterance):
        part = "local"
    if part in ("local", "domain"):
        return await _begin_email_section(db, session, utterance, fix, part)
    if part is None and _corrects_address(utterance):
        # "Actually, it's SAI dot..." in answer to "which part?": the caller is giving the address, not choosing a
        # part. Taken as an edit of what was read back -- seen on a call, this restarted the spelling instead.
        update_collected(session, email_fix=None, email_draft={"local": fix["local"], "domain": fix["domain"], "open": False})
        fragment = nlu.read_email_fragment(utterance)
        fragment.correction = True
        return await _email_progress(db, session, fragment)
    return await _retry_email_from_scratch(db, session)  # both, or still not clear: start the address over


async def _begin_email_section(
    db: AsyncSession, session: VoiceCallSession, utterance: str, fix: dict, part: str
) -> TurnOutcome:
    update_collected(session, email_fix={**fix, "step": part, "tries": 0})
    corrected = None
    if part == "local" and nlu.looks_spelled(utterance):
        corrected = nlu.parse_email_section(utterance, "local")
    elif part == "domain" and re.search(r"\bdot\b|\bperiod\b|\bpoint\b|\.", utterance.lower()):
        corrected = nlu.parse_email_section(utterance, "domain")
    if corrected:
        return await _apply_email_section(db, session, part, corrected)
    session.state = VoiceCallState.COLLECT_EMAIL
    prompt = scripts.EMAIL_FIX_LOCAL if part == "local" else scripts.EMAIL_FIX_DOMAIN
    return TurnOutcome(_speak(session, _ask(session, prompt)))


async def _handle_email_section(
    db: AsyncSession, session: VoiceCallSession, utterance: str, fix: dict
) -> TurnOutcome:
    part = fix["step"]
    result = nlu.interpret_email_section(utterance, part)
    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    if result.failed:
        tries = fix.get("tries", 0) + 1
        if tries >= 2:  # the part could not be understood twice: start the address over
            return await _retry_email_from_scratch(db, session)
        update_collected(session, email_fix={**fix, "tries": tries})
        prompt = scripts.EMAIL_FIX_LOCAL if part == "local" else scripts.EMAIL_FIX_DOMAIN
        return TurnOutcome(_speak(session, _ask(session, f"{scripts.EMAIL_FIX_AGAIN} {prompt}")))
    return await _apply_email_section(db, session, part, result.value)


async def _apply_email_section(db: AsyncSession, session: VoiceCallSession, part: str, section: str) -> TurnOutcome:
    """Put the corrected part together with the other part as it was, and read the whole address back."""
    fix = session.collected["email_fix"]
    local, domain = (section, fix["domain"]) if part == "local" else (fix["local"], section)
    email = nlu.normalize_email(f"{local}@{domain}")
    update_collected(session, email_fix=None, email_fixes=session.collected.get("email_fixes", 0) + 1)
    if email is None or not _ticket_accepts_email(email):  # the parts do not make an address: ask again
        return await _retry_email_from_scratch(db, session)
    return _read_back_email(session, email)  # the whole address is read back again


async def _handle_directory_email_answer(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    """The caller was offered the address on file for them. Yes uses it; anything else forgets it and
    asks the usual question (or takes a new address if they said one in the same breath)."""
    collected = session.collected
    local, domain = nlu.split_spoken_email(utterance)
    if local and domain:
        update_collected(session, email_candidate=None, email_source=None)
        session.state = VoiceCallState.COLLECT_EMAIL
        return await _handle_email(db, session, utterance)
    result = await nlu.interpret_yes_no("Is that still the best email?", utterance)
    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    candidate = collected.get("email_candidate")
    if result.yes_no is True and candidate:
        update_collected(
            session, email=candidate, email_confirmed=True, email_candidate=None, email_source="directory_confirmed"
        )
        return await _finish_collection(db, session)
    update_collected(session, email_candidate=None, email_source=None)
    return _ask_for_email(session, acknowledgement="Okay.", offer=False)


# --- the read-back before creating a ticket -------------------------------------


def _summary_text(session: VoiceCallSession, *, updated: bool = False) -> str:
    """What the agent reads back: who, what, since when, priority and why.

    Built only from validated fields and fixed sentences, never model prose, so
    it cannot say something the priority rules did not decide. Short: it is
    heard, not read, and the caller can interrupt it.
    """
    c = session.collected
    first_name = (c.get("caller_name") or "").split(" ")[0]
    n = len(session.turns or [])

    if updated:
        sentences = [scripts.SUMMARY_UPDATED]
    elif first_name:
        sentences = [scripts.pick(scripts.SUMMARY_INTRO_OPTIONS, n).format(first_name=first_name)]
    else:
        sentences = [scripts.SUMMARY_INTRO_NO_NAME]

    department, issue = c.get("department"), c.get("short_issue")
    if department and issue:
        sentences.append(f"You're in {department}, and you're having trouble with {issue}.")
    elif issue:
        sentences.append(f"You're having trouble with {issue}.")
    elif department:
        sentences.append(f"You're in {department}, reporting an IT problem.")
    else:
        sentences.append("You're reporting an IT problem.")

    started = (c.get("started") or "").strip()
    if started:
        if started.lower().startswith(("since ", "for ")):
            sentences.append(f"It's been going on {started}.")
        else:
            sentences.append(f"It started {started}.")

    assessment = _assessment(session)
    announcement = priority_rules.notice(assessment)
    if announcement:
        sentences.append(announcement)
    elif c.get("work_blocked") is False:
        word = priority_rules.SPOKEN[assessment.priority]
        sentences.append(f"Since you can still work, I'll log it as {word} priority.")
    elif assessment.needs_triage:
        sentences.append(scripts.SUMMARY_FLAGGED_FOR_REVIEW)
    elif "work_impact" in assessment.unverified:
        sentences.append(scripts.SUMMARY_WORK_IMPACT_UNKNOWN)

    if c.get("phone_spoken") and c.get("phone_number") and not c.get("callback_confirmed"):
        # (Once the callback number has been confirmed it has been heard already.)
        sentences.append(f"I'll reach you at {scripts.spoken_phone_number(c['phone_number'])}.")

    if c.get("category") not in (None, "Other") and c.get("category_confidence") in ("medium", "low"):
        sentences.append(f"I'm filing it under {c['category']}.")

    sentences.append(scripts.pick(scripts.SUMMARY_CONFIRM_OPTIONS, n))
    return " ".join(sentences)


async def _finish_collection(
    db: AsyncSession, session: VoiceCallSession, *, preamble: str | None = None
) -> TurnOutcome:
    """Everything needed is gathered. Confirm the callback number, read the ticket
    back, then create it.

    With the read-back on (the default) the caller confirms the whole ticket
    once, and the category question is dropped in favour of a "filing it
    under X" clause in that read-back. With it off, the older behaviour
    applies: confirm an uncertain category, then create.

    Every route to a ticket passes through here, so the callback number is
    checked here, once, before anything else.
    """
    collected = session.collected
    if _callback_number_pending(session):
        return _ask_callback_confirmation(session, preamble=preamble)
    if _is_patient_call(session):
        return await _create_ticket_and_read_back(db, session, preamble=preamble)
    if settings.voice_confirm_summary and not collected.get("summary_confirmed"):
        session.state = VoiceCallState.CONFIRM_SUMMARY
        text = _summary_text(session, updated=bool(collected.get("summary_rounds")))
        update_collected(session, summary_text=text)  # what a correction is interpreted against
        body = _say_then_ask(session, preamble, text) if preamble else _ask(session, text)
        return TurnOutcome(_speak(session, body))

    if (
        not settings.voice_confirm_summary
        and collected.get("category_confidence") in ("medium", "low")
        and collected.get("category") != "Other"
    ):
        # Never "Is this about Other?": confirming the catch-all tells the
        # caller nothing, and a "no" changes nothing.
        session.state = VoiceCallState.CONFIRM_CATEGORY
        prompt = scripts.CATEGORY_CONFIRM.format(category=collected.get("category"))
        body = _say_then_ask(session, preamble, prompt) if preamble else _ask(session, prompt)
        return TurnOutcome(_speak(session, body))

    return await _create_ticket_and_read_back(db, session, preamble=preamble)


def _summary_server_detail(session: VoiceCallSession, utterance: str) -> TurnOutcome | None:
    """"The server name is 172.22.6.203." at the read-back: that one detail is recorded (or, if it is a new
    address, asked about), then only "Is everything else correct?" -- not the whole summary again, and nothing
    else is changed or restated as certain."""
    c = session.collected
    state = c.get("diagnostics") or {}
    names = diagnostics.hostnames(utterance)
    if (state.get("playbook") not in ("server_storage", "server_access") or not names
            or not re.search(r"\b(?:server|name|address|ip|host)\b", utterance or "", re.IGNORECASE)):
        return None
    new = names[0]
    values = dict(state.get("values") or {})
    listed = lambda key: [n for n in (values.get(key) or "").split(", ") if n]  # noqa: E731
    known = [values.get("server"), values.get("server_name"), *listed("server_aliases"), *listed("additional_servers"),
             *listed("server_unclarified")]
    update_collected(session, summary_rounds=c.get("summary_rounds", 0) + 1, summary_awaiting_change=False)
    if not any(new.lower() == str(k).lower() for k in known if k):
        # A new address: the same question as anywhere else (another server, a correction, the same server).
        late = {"state": VoiceCallState.CONFIRM_SUMMARY.value, "prompt": scripts.SUMMARY_REST_CORRECT,
                "said": utterance, "host": new, "step": "kind", "kind": "additional"}
        update_collected(session, late_server=late)
        session.state = VoiceCallState.COLLECT_DIAGNOSTICS
        current = values.get("server")
        question = (f"Is {new} another server that's affected as well, or should it replace {current}?"
                    if current else scripts.LATE_SERVER_NAME)
        return TurnOutcome(_speak(session, _ask(session, question)))
    if re.search(r"\bname\b", utterance or "", re.IGNORECASE) and new.lower() != str(values.get("server", "")).lower():
        # The caller says this is the server's name: no longer an open question.
        values["server_unclarified"] = ", ".join(n for n in listed("server_unclarified") if n.lower() != new.lower()) or None
        values.setdefault("server_name", None)
        if not values.get("server_name"):
            values["server_name"], values["server_name_said"] = new, diagnostics.clean(utterance)
        update_collected(session, diagnostics={**state, "values": values})
    session.state = VoiceCallState.CONFIRM_SUMMARY
    noted = scripts.SUMMARY_SERVER_NOTED.format(server=new)
    return TurnOutcome(_speak(session, _ask(session, f"{noted} {scripts.SUMMARY_REST_CORRECT}")))


async def _handle_summary_confirmation(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    """Yes -> file it. A change -> apply it, repeat anything doubtful back, read
    the ticket again. Bounded: after MAX_SUMMARY_CORRECTIONS the ticket is
    filed as it stands and marked unverified."""
    collected = session.collected
    verdict = nlu.quick_yes_no(utterance, strict=True)
    if verdict is True:
        return await _confirm_summary(db, session)
    if nlu.mentions_escalation(utterance):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    if collected.get("summary_rounds", 0) >= MAX_SUMMARY_CORRECTIONS:
        update_collected(session, summary_unresolved=True)
        return await _confirm_summary(db, session, preamble=scripts.SUMMARY_LEAVE_AS_IS)

    targeted = _summary_server_detail(session, utterance)
    if targeted is not None:
        return targeted

    if verdict is False and not collected.get("summary_awaiting_change"):
        # A bare "no": ask what is wrong rather than guess.
        update_collected(session, summary_awaiting_change=True)
        return TurnOutcome(_speak(session, _ask(session, scripts.SUMMARY_WHAT_TO_CHANGE)))

    result = await nlu.interpret_summary_correction(utterance, collected.get("summary_text") or "")
    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    changes = result.extras.get("changes") or {}

    if not changes:
        if result.extras.get("nothing_to_change"):
            return await _confirm_summary(db, session)
        if collected.get("summary_clarified"):
            update_collected(session, summary_unresolved=True)
            return await _confirm_summary(db, session, preamble=scripts.SUMMARY_LEAVE_AS_IS)
        update_collected(session, summary_clarified=True, summary_awaiting_change=True)
        return TurnOutcome(_speak(session, _ask(session, scripts.SUMMARY_WHICH_PART)))

    _apply_corrections(session, changes)
    update_collected(
        session,
        summary_rounds=collected.get("summary_rounds", 0) + 1,
        summary_awaiting_change=False,
        summary_clarified=False,
    )
    # Back through the normal steps: a changed name is read back spelled, a
    # changed phone number is kept, then the ticket is read again.
    return await _next_step(db, session)


def _apply_corrections(session: VoiceCallSession, changes: dict) -> None:
    if changes.get("caller_name"):
        if _set_name(session, changes["caller_name"], model_confidence=None):
            update_collected(session, name_confirmed_as=None)
        else:
            # "Change my name" read as the name "Name": the caller wants it changed, so ask them to spell it.
            update_collected(session, name_confirmed_as=None, name_confirm_pending=True, name_spell_first=True)
    if changes.get("department"):
        _set_department(session, changes["department"])
    if changes.get("short_issue"):
        update_collected(session, short_issue=changes["short_issue"])
    if changes.get("description"):
        update_collected(session, description=changes["description"])
    if changes.get("category"):
        update_collected(session, category=changes["category"], category_confidence="high")
    if changes.get("started"):
        update_collected(session, started=changes["started"])
    if "work_blocked" in changes:
        session.collected = facts_mod.apply_fact(
            session.collected,
            facts_mod.Fact.known(
                facts_mod.WORK_BLOCKED,
                changes["work_blocked"],
                source=facts_mod.Source.CORRECTION,
                evidence=changes.get("work_blocked_evidence"),
            ),
        )
    if changes.get("phone_number"):
        update_collected(session, phone_number=changes["phone_number"], phone_spoken=True, phone_skipped=False)
        # A number given at the read-back replaces the confirmed one, so it is confirmed in its turn.
        update_collected(
            session, callback_confirmed=False, callback_number=None, callback_candidate=None, callback_fallback=False
        )
    _apply_priority_rules(session)


async def _confirm_summary(
    db: AsyncSession, session: VoiceCallSession, *, preamble: str | None = None
) -> TurnOutcome:
    update_collected(session, summary_confirmed=True, summary_awaiting_change=False)
    return await _create_ticket_and_read_back(db, session, preamble=preamble)


async def _handle_category_confirmation(
    db: AsyncSession, session: VoiceCallSession, utterance: str
) -> TurnOutcome:
    result = await nlu.interpret_yes_no("Is this about that category?", utterance)

    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    if result.yes_no is False:
        # Don't play twenty questions -- a human reroutes faster.
        update_collected(session, category="Other")

    return await _create_ticket_and_read_back(db, session)


async def _handle_anything_else(
    db: AsyncSession, session: VoiceCallSession, utterance: str
) -> TurnOutcome:
    result = await nlu.interpret_yes_no("Is there anything else I can help you with?", utterance)

    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    if result.yes_no is True:
        # Second ticket on the same call: clear the issue slots but keep who
        # the caller is, and don't ask for department or email again.
        keep = (
            "caller_name", "name_confidence", "name_confirmed_as", "name_unverified", "department",
            "department_verified", "phone_number", "phone_spoken", "phone_skipped", "email", "email_confirmed",
            "callback_confirmed", "callback_number", "callback_fallback",
            "ticket_ids", "caller_type", "patient_category",
            "directory", "identity_confirmed", "identity_assurance", "directory_identity", "identity_rounds",
            "extension", "extension_source", "extension_note",
            "identified_by_routing", "patient_limitation_told",
        )
        session.collected = {k: session.collected.get(k) for k in keep if session.collected.get(k) is not None}
        session.collected["asked"] = ["department", "email"]
        session.collected["ticket_filed"] = False  # the next issue gets its own ticket
        session.email_attempt_count = 0
        if session.collected.get("identified_by_routing") and _is_patient_call(session):
            # A second request from a patient starts at "what do you need?", not at the IT question.
            session.state = VoiceCallState.PATIENT_INTENT
            return TurnOutcome(_speak(session, _ask(session, scripts.PATIENT_INTENT_ASK_AGAIN)))
        session.state = VoiceCallState.COLLECT_DESCRIPTION
        return TurnOutcome(_speak(session, _ask(session, scripts.DESCRIPTION_ASK)))

    session.state = VoiceCallState.COMPLETED
    return TurnOutcome(_speak(session, reply.say_and_hangup(scripts.GOODBYE)))


async def _caller_leaving(db: AsyncSession, session: VoiceCallSession) -> TurnOutcome:
    """"Thank you. Bye bye." mid-intake. Don't take it for an answer.

    If they had already described the problem and we can reach them, the
    ticket is saved with what we have (flagged incomplete) and they hear the
    number; otherwise it's a polite goodbye.
    """
    collected = session.collected
    if collected.get("escalation_pending"):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
    if issue_filed(session):
        session.state = VoiceCallState.COMPLETED
        return TurnOutcome(_speak(session, reply.say_and_hangup(scripts.GOODBYE)))

    reachable = bool(collected.get("phone_number")) or caller_id_is_usable(session.from_number)
    if collected.get("description") and reachable:
        if collected.get("caller_name"):
            # Everything required was already collected (the caller left at an
            # optional question such as email): an ordinary ticket at the
            # priority we assessed, not an "incomplete" one bumped to High.
            if session.state == VoiceCallState.CONFIRM_SUMMARY:
                update_collected(session, summary_unanswered=True)  # read back, then the caller left: not confirmed
            else:
                update_collected(session, summary_skipped=True)
            ticket = await _create_ticket(db, session)
        else:
            ticket = await _create_ticket(
                db,
                session,
                escalation_note="INCOMPLETE VOICE INTAKE - caller ended the call before intake finished.",
                raise_priority=False,  # they chose to leave; automation did not fail them
            )
        _record_ticket(session, ticket)
        session.state = VoiceCallState.COMPLETED
        leaving = scripts.PATIENT_LEAVING_WITH_REQUEST if _is_patient_call(session) else scripts.LEAVING_WITH_TICKET
        line = leaving.format(ticket_number=scripts.spoken_ticket_number(ticket.ticket_number))
        return TurnOutcome(_speak(session, reply.say_and_hangup(line)), ticket.id)

    session.state = VoiceCallState.ABANDONED
    return TurnOutcome(_speak(session, reply.say_and_hangup(scripts.LEAVING_NO_TICKET)))


# --- ticket creation ------------------------------------------------------


async def _create_ticket_and_read_back(
    db: AsyncSession, session: VoiceCallSession, *, preamble: str | None = None
) -> TurnOutcome:
    patient = _is_patient_call(session)
    anything_else = scripts.PATIENT_ANYTHING_ELSE if patient else scripts.ANYTHING_ELSE

    if issue_filed(session):
        ticket = await db.get(Ticket, session.ticket_id)
        read_back_tpl = scripts.PATIENT_READ_BACK if patient else scripts.READ_BACK
        read_back = read_back_tpl.format(
            ticket_number=scripts.spoken_ticket_number(ticket.ticket_number)
        )
        return TurnOutcome(
            _speak(session, _say_then_ask(session, read_back, anything_else)), session.ticket_id
        )

    ticket = await _create_ticket(db, session)
    _record_ticket(session, ticket)
    session.state = VoiceCallState.ANYTHING_ELSE

    lines = [preamble] if preamble else []
    if patient:
        lines.append(scripts.PATIENT_CREATING_TICKET)
    elif session.collected.get("summary_confirmed"):
        lines.append(scripts.SUMMARY_FILING)
    else:
        announcement = priority_rules.notice(_assessment(session))
        if announcement:
            lines.append(announcement)
        lines.append(scripts.CREATING_TICKET)

    read_back_tpl = scripts.PATIENT_READ_BACK if patient else scripts.READ_BACK
    lines.append(
        read_back_tpl.format(ticket_number=scripts.spoken_ticket_number(ticket.ticket_number))
    )

    return TurnOutcome(
        _speak(session, _say_then_ask(session, " ".join(lines), anything_else)), ticket.id
    )


async def _create_ticket(
    db: AsyncSession,
    session: VoiceCallSession,
    *,
    escalation_note: str | None = None,
    raise_priority: bool = True,
) -> Ticket:
    """Build a ticket from collected slots via the unchanged Phase 1 service.

    Patient tickets get a simplified description (no intake details, no priority
    assessment, no triage note) and always land at MEDIUM priority.

    A ticket carrying an `escalation_note` is a callback request or salvaged
    call, and is raised to at least High so it doesn't wait in a normal queue
    (automation couldn't serve that caller). `raise_priority=False` keeps the
    assessed priority for a caller who simply chose to leave.
    """
    collected = session.collected
    patient = _is_patient_call(session)

    description = collected.get("description") or ""
    if escalation_note:
        description = f"{escalation_note}\n\n{description}".strip()
    if not patient:
        if collected.get("details"):
            description = f"{description}\n\n{DETAILS_MARKER}\n{collected['details']}".strip()
        details = _intake_details(collected)
        if details:
            description = f"{description}\n\n{INTAKE_MARKER}\n{details}".strip()
    description = f"{description}\n\n{TRANSCRIPT_MARKER}\n{transcript_text(session)}".strip()
    if not patient and collected.get("needs_triage"):
        description = f"{priority_rules.triage_note(_assessment(session))}\n\n{description}".strip()

    if patient:
        priority = Priority.MEDIUM
    else:
        priority = Priority(collected.get("priority") or Priority.MEDIUM.value)
    if escalation_note and raise_priority:
        priority = Priority.URGENT if priority == Priority.URGENT else Priority.HIGH

    # The number to call: the one the caller confirmed, else the one we had (caller ID or spoken).
    callback_number = collected.get("callback_number")
    phone = callback_number or collected.get("phone_number") or (
        session.from_number if caller_id_is_usable(session.from_number) else "unknown"
    )
    # What the call came in from, kept as-is for auditing ("anonymous" says it was withheld).
    inbound = (session.from_number or "").strip()
    caller_number = None if inbound in ("", "unknown") else inbound[:32]

    category_name = (
        collected.get("patient_category") or "Other Patient Support"
    ) if patient else collected.get("category")

    # Stored as given only when it is a real CallerType. Anything else (a value written by older code, or a
    # corrupted session) is saved as unknown (NULL) rather than crashing the call before its ticket exists;
    # the call's own `collected` is left exactly as it was.
    caller_type_value = _known_caller_type(collected.get("caller_type"))
    if caller_type_value is None and collected.get("caller_type"):
        logger.warning("Unrecognised caller type on call %s; ticket saved without one", getattr(session, "call_id", "?"))

    payload = TicketCreate(
        caller_name=collected.get("caller_name") or "Unknown caller (voice)",
        phone_number=phone,
        # An address the caller has not said yes to (a ticket made while it is still being read back,
        # because they hung up or asked for a person) is not an address we have: the notification
        # then says "Not provided".
        email=collected.get("email") if collected.get("email_confirmed") else None,
        category_id=await _category_id(db, category_name),
        priority=priority,
        description=description[:10_000],
    )

    source = TicketSource.SIMULATOR if session.is_simulated else TicketSource.PHONE
    return await ticket_service.create_ticket(
        db, payload, source=source, caller_number=caller_number,
        callback_number=callback_number, caller_type=caller_type_value, commit=False,
    )


def _intake_details(collected: dict) -> str:
    """Structured facts the tickets table has no columns for, as labelled lines
    at the top of the description where agents (and the AI summary) see them.

    Anything the agent could not verify is said so, rather than left looking certain.
    """
    blocked = collected.get("work_blocked")
    unverified = collected.get("priority_unverified") or []
    department = collected.get("department")
    if department and collected.get("department_verified") is False:
        department = f"{department} (unverified: not on HFMG's department list)"
    elif department and not departments.configured():
        department = f"{department} (not checked: no HFMG department list is configured)"
    elif not department and collected.get("caller_type") != "PATIENT_SUPPORT":
        department = "not given"
    reason = collected.get("priority_reason")
    priority = collected.get("priority")
    priority_line = None
    if priority:
        priority_line = f"{priority.title()}" + (f": {reason}" if reason else "")

    who = collected.get("directory_identity") or {}
    confirmed = bool(collected.get("identity_confirmed"))
    lines = [
        ("Department", department),
        # The caller said yes to a directory entry. That is all it is: not authentication.
        ("Identity", "directory entry confirmed by the caller; not authenticated" if confirmed else None),
        ("Extension", _extension_detail(collected, confirmed, who)),
        ("Location", who.get("location") if confirmed else None),
        ("Teams", ", ".join(who.get("teams") or [])[:300] if confirmed else None),
        ("Started", collected.get("started")),
        ("Work blocked", None if blocked is None else ("yes" if blocked else "no")),
        ("Impact", collected.get("impact")),
        ("Priority", priority_line),
        ("Priority basis", collected.get("priority_basis")),
        ("Work impact", "not confirmed (the caller did not say whether this stops them working)" if "work_impact" in unverified else None),
        ("Patient impact", "not confirmed (the caller did not say whether patient care is affected)" if "patient_impact" in unverified else None),
        ("Callback number", "not captured (the caller could not give one)" if collected.get("phone_skipped") else None),
        ("Callback number", "not confirmed by the caller; using the number on file" if collected.get("callback_fallback") else None),
        ("Name", "spelling could not be verified with the caller" if collected.get("name_unverified") else None),
        ("Read-back", "caller said the summary was not fully correct; details unverified" if collected.get("summary_unresolved") else None),
        ("Read-back", "not done: the caller ended the call first" if collected.get("summary_skipped") else None),
        ("Read-back", "read to the caller, no answer: not confirmed" if collected.get("summary_unanswered") else None),
    ]
    lines += diagnostics.ticket_lines(collected.get("diagnostics"), collected.get("description"), collected.get("category"))
    return "\n".join(f"{label}: {value}" for label, value in lines if value)


async def salvage_abandoned_call(db: AsyncSession, session: VoiceCallSession) -> Ticket:
    """Create a ticket from a call that dropped after the problem was described."""
    ticket = await _create_ticket(
        db,
        session,
        escalation_note="INCOMPLETE VOICE INTAKE - caller disconnected before intake finished.",
    )
    _record_ticket(session, ticket)
    return ticket


async def _category_id(db: AsyncSession, name: str | None) -> uuid.UUID:
    stmt = select(Category).where(Category.name == (name or "Other"))
    category = (await db.execute(stmt)).scalar_one_or_none()
    if category is None:
        category = (await db.execute(select(Category).where(Category.name == "Other"))).scalar_one()
    return category.id


# --- failure and escalation ----------------------------------------------


async def _handle_failure(db: AsyncSession, session: VoiceCallSession) -> TurnOutcome:
    """Silence or an uninterpretable answer to a required question."""
    session.misunderstanding_count += 1

    if session.misunderstanding_count >= settings.voice_max_misunderstandings:
        return await escalate(db, session, EscalationReason.REPEATED_MISUNDERSTANDING)

    retry_index = min(session.misunderstanding_count - 1, 1)
    retries = {
        VoiceCallState.COLLECT_DESCRIPTION: scripts.DESCRIPTION_RETRY,
        VoiceCallState.COLLECT_NAME: scripts.NAME_RETRY,
        VoiceCallState.COLLECT_PHONE: scripts.PHONE_RETRY,
    }
    if session.state in retries:
        prompt = retries[session.state][retry_index]
    else:
        # Any other question (the email, the callback number, ...): ask *that* question again. Never the opening
        # "what's going wrong?" -- the call has not lost track of what it asked.
        asked = _last_agent_line(session).removeprefix(diagnostics.CLARIFY).strip()
        prompt = f"{diagnostics.CLARIFY} {asked}" if asked else scripts.DESCRIPTION_RETRY[retry_index]
    return TurnOutcome(_speak(session, _ask(session, prompt)))


async def escalate(
    db: AsyncSession, session: VoiceCallSession, reason: EscalationReason
) -> TurnOutcome:
    """Create a callback request and hand off. The caller always gets a number.

    A callback needs a number to call. If the caller asked for a person and we
    have none (caller ID withheld, not asked yet), ask for it once first --
    otherwise the ticket promises a callback nobody can make. Seen in a real
    test call: "I need IT experts to call back me" produced a callback ticket
    with no number.
    """
    phone = session.collected.get("phone_number") or (
        session.from_number if caller_id_is_usable(session.from_number) else None
    )
    if (
        reason == EscalationReason.CALLER_REQUESTED
        and not phone
        and not session.collected.get("callback_number_asked")
    ):
        update_collected(session, callback_number_asked=True, escalation_pending=True)
        session.state = VoiceCallState.COLLECT_PHONE
        return TurnOutcome(_speak(session, _ask(session, _escalation_lines(session)["phone_ask"])))

    session.escalated = True
    session.escalation_reason = reason
    session.state = VoiceCallState.ESCALATED
    update_collected(session, escalation_pending=False)

    notes = {
        EscalationReason.CALLER_REQUESTED: "CALLBACK REQUESTED - caller asked to speak with a person.",
        EscalationReason.REPEATED_MISUNDERSTANDING: (
            "CALLBACK REQUESTED - automated intake could not understand the caller after 3 attempts."
        ),
        EscalationReason.SYSTEM_ERROR: (
            "CALLBACK REQUESTED - automated intake hit a system error before completing."
        ),
    }

    if issue_filed(session):
        ticket = await db.get(Ticket, session.ticket_id)
    else:
        ticket = await _create_ticket(db, session, escalation_note=notes[reason])
        _record_ticket(session, ticket)

    phone = session.collected.get("callback_number") or session.collected.get("phone_number") or phone

    lines = _escalation_lines(session)
    if phone:
        template = lines["requested"] if reason == EscalationReason.CALLER_REQUESTED else lines["misunderstood"]
        opener = template.format(phone=scripts.spoken_phone_number(phone))
    elif reason == EscalationReason.CALLER_REQUESTED:
        # The caller was perfectly clear; never tell them we didn't understand.
        opener = lines["no_number"]
    else:
        opener = lines["misunderstood_no_number"]

    read_back = lines["read_back"].format(ticket_number=scripts.spoken_ticket_number(ticket.ticket_number))
    return TurnOutcome(
        _speak(session, reply.say_and_hangup(opener, read_back, lines["goodbye"])), ticket.id
    )


# Who the escalation lines speak to. A patient hears about the support team and a request, an employee about
# the IT team and a ticket. A caller whose type is not known -- not asked yet, not classified yet, or a stored
# value that is not a CallerType -- hears neutral lines: the type is never guessed here, only read.
_ESCALATION_LINES = {
    CallerType.PATIENT_SUPPORT.value: {
        "requested": scripts.PATIENT_ESCALATION,
        "phone_ask": scripts.PATIENT_ESCALATION_PHONE_ASK,
        "no_number": scripts.PATIENT_ESCALATION_NO_NUMBER,
        "misunderstood": scripts.PATIENT_ESCALATION_MISUNDERSTOOD,
        "misunderstood_no_number": scripts.PATIENT_ESCALATION_NO_CALLBACK_NUMBER,
        "read_back": scripts.PATIENT_ESCALATION_READ_BACK,
        "goodbye": scripts.PATIENT_GOODBYE,
    },
    CallerType.INTERNAL_IT.value: {
        "requested": scripts.ESCALATION_CALLER_REQUESTED,
        "phone_ask": scripts.ESCALATION_PHONE_ASK,
        "no_number": scripts.ESCALATION_CALLER_REQUESTED_NO_NUMBER,
        "misunderstood": scripts.ESCALATION_MISUNDERSTOOD,
        "misunderstood_no_number": scripts.ESCALATION_NO_CALLBACK_NUMBER,
        "read_back": scripts.ESCALATION_READ_BACK,
        "goodbye": scripts.GOODBYE,
    },
    None: {
        "requested": scripts.NEUTRAL_ESCALATION,
        "phone_ask": scripts.NEUTRAL_ESCALATION_PHONE_ASK,
        "no_number": scripts.NEUTRAL_ESCALATION_NO_NUMBER,
        "misunderstood": scripts.NEUTRAL_ESCALATION_MISUNDERSTOOD,
        "misunderstood_no_number": scripts.NEUTRAL_ESCALATION_NO_CALLBACK_NUMBER,
        "read_back": scripts.NEUTRAL_ESCALATION_READ_BACK,
        "goodbye": scripts.GOODBYE,
    },
}


def _known_caller_type(value) -> CallerType | None:
    """The stored caller type if it is exactly one of ours, else None. Never guesses: "PATIENT",
    "patient_support" or anything else unrecognised is unknown, not a patient."""
    return next((member for member in CallerType if isinstance(value, str) and value == member.value), None)


def _escalation_lines(session: VoiceCallSession) -> dict[str, str]:
    known = _known_caller_type((session.collected or {}).get("caller_type"))
    return _ESCALATION_LINES[known.value if known else None]


def _wants_human(utterance: str, result: nlu.TurnResult) -> bool:
    """Decide whether the caller is asking for a person.

    The model's semantic judgement is authoritative -- keyword matching alone
    would misfire on descriptions like "the person at the front desk can't
    print". The keyword list is only a fallback for when the NLU call itself
    failed, so an explicit request still works while the model is unreachable.
    """
    if result.escalation_requested:
        return True
    return result.failed and nlu.mentions_escalation(utterance)


def _ask(session: VoiceCallSession, prompt: str) -> reply.Reply:
    return reply.ask(prompt)


def _say_then_ask(session: VoiceCallSession, statement: str, prompt: str) -> reply.Reply:
    return reply.say_then_ask(statement, prompt)


def _speak(session: VoiceCallSession, reply_: reply.Reply) -> reply.Reply:
    """Record what the agent said, for the transcript on the ticket."""
    record_turn(session, role="agent", text=reply_.text)
    return reply_
