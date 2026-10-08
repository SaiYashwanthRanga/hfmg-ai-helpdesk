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

import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import (
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
from app.voice import departments, names, nlu, reply, scripts
from app.voice import facts as facts_mod
from app.voice import priority as priority_rules
from app.services.ticket_text import DETAILS_MARKER, INTAKE_MARKER, TRANSCRIPT_MARKER
from app.voice.session import caller_id_is_usable, record_turn, transcript_text, update_collected

logger = logging.getLogger("hfmg.voice.orchestrator")

settings = get_settings()

#: Corrections to the read-back before the agent files it as-is and notes it.
MAX_SUMMARY_CORRECTIONS = 2
#: Read-backs of a name before the agent accepts the caller's own spelling.
MAX_NAME_ROUNDS = 2


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


async def start_call(session: VoiceCallSession) -> TurnOutcome:
    """Turn 0: greet and wait for the caller to describe their problem."""
    session.state = VoiceCallState.COLLECT_DESCRIPTION
    record_turn(session, role="agent", text=scripts.GREETING)
    return TurnOutcome(_ask(session, scripts.GREETING))


_GOODBYE_STATES = (
    VoiceCallState.COLLECT_DESCRIPTION,
    VoiceCallState.COLLECT_DETAILS,
    VoiceCallState.COLLECT_NAME,
    VoiceCallState.CONFIRM_NAME,
    VoiceCallState.COLLECT_PHONE,
    VoiceCallState.CONFIRM_CALLBACK_NUMBER,
    VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER,
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
) -> TurnOutcome:
    """Interpret one caller utterance and advance the conversation."""
    utterance = (utterance or "").strip()
    record_turn(session, role="caller", text=utterance, confidence=confidence)

    if session.state in (VoiceCallState.ESCALATED, VoiceCallState.COMPLETED):
        # Replay of an already-terminal turn (gateway retry). Say goodbye again
        # rather than re-running any of the work.
        return TurnOutcome(_speak(session, reply.say_and_hangup(scripts.GOODBYE)))

    if not utterance:
        if session.collected.get("escalation_pending"):
            return await escalate(db, session, EscalationReason.CALLER_REQUESTED)
        if session.state == VoiceCallState.CONFIRM_SUMMARY:
            # Silence after the read-back is not an objection (and may be a
            # dropped line): file what we have, noted as unconfirmed.
            update_collected(session, summary_unresolved=True)
            return await _confirm_summary(db, session)
        if session.state in (VoiceCallState.COLLECT_DETAILS, VoiceCallState.CONFIRM_NAME):
            # Optional information / confirmation: silence moves the call on
            # rather than counting as a misunderstanding.
            update_collected(session, name_spell_step=None, name_reconfirm=False, name_confirm_pending=False)
            if session.collected.get("pending_question"):
                _record_clarification(session, session.collected["pending_question"], "unclear", "")
            return await _next_step(db, session)
        if session.state == VoiceCallState.COLLECT_PHONE:
            return await _phone_not_understood(db, session, "")
        return await _handle_failure(db, session)

    if session.state in _GOODBYE_STATES and nlu.is_goodbye(utterance):
        return await _caller_leaving(db, session)

    handlers = {
        VoiceCallState.COLLECT_DESCRIPTION: _handle_description,
        VoiceCallState.COLLECT_DETAILS: _handle_details,
        VoiceCallState.COLLECT_NAME: _handle_name,
        VoiceCallState.CONFIRM_NAME: _handle_name_confirmation,
        VoiceCallState.COLLECT_PHONE: _handle_phone,
        VoiceCallState.CONFIRM_CALLBACK_NUMBER: _handle_callback_confirmation,
        VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER: _handle_alternate_callback,
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

    return await handler(db, session, utterance)


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
) -> None:
    """Record the caller's name and decide whether it needs reading back.

    Only names we are unsure of cost the caller a turn (app/voice/names.py).
    """
    confidence = names.name_confidence(
        name,
        spelled=spelled,
        model_confidence=model_confidence,
        stt_confidence=_last_caller_confidence(session),
    )
    already_confirmed = (session.collected.get("name_confirmed_as") or "").lower() == name.lower()
    update_collected(
        session,
        caller_name=name,
        name_confidence=confidence,
        name_confirm_pending=bool(settings.voice_confirm_name and confidence == "low" and not already_confirmed),
    )


def _set_department(session: VoiceCallSession, raw: str | None) -> None:
    """Map what the caller said onto HFMG's departments; flag it when it doesn't match."""
    match = departments.canonicalize(raw)
    if match is not None:
        update_collected(session, department=match.name, department_verified=match.verified)


# --- state handlers -------------------------------------------------------


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
    return await _next_step(db, session, acknowledgement=_acknowledgement(session))


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
        affected_scope=extras.get("affected_scope"),
        patient_care_affected=extras.get("patient_care_affected"),
    )
    update_collected(
        session,
        description=result.value,
        category=result.category,
        category_confidence=result.confidence,
        model_priority=(result.priority or Priority.MEDIUM).value,
        short_issue=extras.get("short_issue"),
        started=extras.get("started"),
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
    if not collected.get("department") and extras.get("department"):
        _set_department(session, extras["department"])
    _apply_priority_rules(session)


async def _handle_details(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    """When it started / whether the caller can work. Optional: never retried."""
    if session.collected.get("pending_question"):
        return await _handle_clarification(db, session, utterance)
    asked = _details_question(session)
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
            "affected_scope": result.extras.get("affected_scope"),
        }
        update_collected(session, **{k: v for k, v in values.items() if v is not None})
        _apply_priority_rules(session)
    return await _next_step(db, session)


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
    asked = scripts.DEPARTMENT_ASK_NO_NAME if name_known else scripts.NAME_ASK
    result = await nlu.interpret_name(utterance, asked=asked)

    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    department = result.extras.get("department")
    if result.value and not name_known:
        _set_name(session, result.value, model_confidence=result.extras.get("name_confidence"))
    if department:
        _set_department(session, department)
    if not session.collected.get("caller_name"):
        # The name is required; the department alone is not enough.
        return await _handle_failure(db, session)
    return await _next_step(db, session)


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

    if nlu.looks_spelled(utterance):
        # "No, it's Y A S H W A N T H" -- corrected in the same breath.
        spelled = nlu.decode_spelled(utterance)
        if spelled:
            return _reconfirm_name(session, _replace_closest_part(collected["caller_name"], spelled))

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
    if fix.value and fix.value.lower() != collected["caller_name"].lower():
        return _reconfirm_name(session, fix.value)
    answer = await nlu.interpret_yes_no("Is that name right?", utterance)
    if answer.yes_no is True:
        return await _name_confirmed(db, session)
    return _ask_to_spell(session)


async def _handle_name_reconfirmation(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    """The corrected name was read back. Yes -> done. No again -> another try, then accept."""
    if nlu.looks_spelled(utterance):
        spelled = nlu.decode_spelled(utterance)
        if spelled:
            return _reconfirm_name(session, _replace_closest_part(session.collected["caller_name"], spelled))
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


def _ask_to_spell(session: VoiceCallSession) -> TurnOutcome:
    update_collected(session, name_spell_step="first", name_reconfirm=False)
    session.state = VoiceCallState.CONFIRM_NAME
    return TurnOutcome(_speak(session, _ask(session, scripts.NAME_SPELL_FIRST)))


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
    return _reconfirm_name(session, " ".join(parts))


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
    return bool(
        settings.voice_confirm_callback
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
        prompt = scripts.CALLBACK_CONFIRM.format(number=scripts.spoken_phone_number(collected["phone_number"]))
    body = _say_then_ask(session, preamble, prompt) if preamble else _ask(session, prompt)
    return TurnOutcome(_speak(session, body))


def _ask_alternate_callback(session: VoiceCallSession) -> TurnOutcome:
    session.state = VoiceCallState.COLLECT_ALTERNATE_CALLBACK_NUMBER
    return TurnOutcome(_speak(session, _ask(session, scripts.CALLBACK_ASK_OTHER)))


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


async def _handle_callback_confirmation(
    db: AsyncSession, session: VoiceCallSession, utterance: str
) -> TurnOutcome:
    collected = session.collected
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

    Order: when it started / can you work (asked once, only if not
    volunteered) -> name and department (department asked once) -> read back a
    doubtful name -> callback number (skipped when caller ID gave one) ->
    email (asked once) -> read the ticket back -> create it.
    """
    collected = session.collected

    def ask(state: VoiceCallState, prompt: str) -> TurnOutcome:
        session.state = state
        if acknowledgement:
            prompt = f"{acknowledgement} {prompt}"
        return TurnOutcome(_speak(session, _ask(session, prompt)))

    if (not collected.get("started") or _blocked_needed(collected)) and not _asked(session, "details"):
        _mark_asked(session, "details")
        if settings.voice_strict_extraction and _blocked_needed(collected):
            _mark_clarify_ask(session, "blocked", counts_toward_cap=False)  # the details question asks it too
            if collected.get("started"):
                # Only "can you work" is missing: ask it directly, with one clear meaning for yes.
                update_collected(session, pending_question="blocked")
                return ask(VoiceCallState.COLLECT_DETAILS, _clarification_prompt(session, "blocked"))
        return ask(VoiceCallState.COLLECT_DETAILS, _details_question(session))

    clarification = _next_clarification(session)
    if clarification:
        _mark_clarify_ask(session, clarification)
        update_collected(session, pending_question=clarification)
        return ask(VoiceCallState.COLLECT_DETAILS, _clarification_prompt(session, clarification))

    if not collected.get("caller_name"):
        _mark_asked(session, "department")  # asked together with the name
        return ask(VoiceCallState.COLLECT_NAME, scripts.pick(scripts.NAME_ASK_OPTIONS, len(session.turns or [])))

    if not collected.get("department") and not _asked(session, "department"):
        _mark_asked(session, "department")
        first_name = collected["caller_name"].split(" ")[0]
        return ask(VoiceCallState.COLLECT_NAME, scripts.DEPARTMENT_ASK.format(first_name=first_name))

    if settings.voice_confirm_name and collected.get("name_confirm_pending"):
        spelled = scripts.spelled_name(collected["caller_name"])
        prompt = scripts.pick(scripts.NAME_CONFIRM_OPTIONS, len(session.turns or [])).format(spelled=spelled)
        return ask(VoiceCallState.CONFIRM_NAME, prompt)

    if not collected.get("phone_number") and not collected.get("phone_skipped"):
        return ask(VoiceCallState.COLLECT_PHONE, scripts.PHONE_ASK)

    if not collected.get("email") and not _asked(session, "email"):
        _mark_asked(session, "email")
        return _ask_for_email(session, acknowledgement=acknowledgement)

    return await _finish_collection(db, session, preamble=acknowledgement)


def _ask_for_email(session: VoiceCallSession, *, acknowledgement: str | None = None) -> TurnOutcome:
    first_name = (session.collected.get("caller_name") or "").split(" ")[0]
    prompt = (
        scripts.EMAIL_ASK.format(first_name=first_name) if first_name else scripts.EMAIL_ASK_NO_NAME
    )
    if acknowledgement:
        prompt = f"{acknowledgement} {prompt}"
    session.state = VoiceCallState.COLLECT_EMAIL
    return TurnOutcome(_speak(session, _ask(session, prompt)))


async def _handle_email(db: AsyncSession, session: VoiceCallSession, utterance: str) -> TurnOutcome:
    result = await nlu.interpret_email(utterance)

    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    if result.extras.get("declined"):
        return await _finish_collection(db, session)

    session.email_attempt_count += 1

    if result.failed:
        # Email is optional, so a failure here never counts toward escalation.
        if session.email_attempt_count >= settings.voice_max_email_attempts:
            return await _finish_collection(db, session, preamble=scripts.EMAIL_GIVE_UP)
        return TurnOutcome(_speak(session, _ask(session, scripts.EMAIL_RETRY)))

    update_collected(session, email=result.value)
    session.state = VoiceCallState.CONFIRM_EMAIL
    prompt = scripts.EMAIL_CONFIRM.format(email=scripts.spoken_email(result.value))
    return TurnOutcome(_speak(session, _ask(session, prompt)))


async def _handle_email_confirmation(
    db: AsyncSession, session: VoiceCallSession, utterance: str
) -> TurnOutcome:
    result = await nlu.interpret_yes_no("Is that email correct?", utterance)

    if _wants_human(utterance, result):
        return await escalate(db, session, EscalationReason.CALLER_REQUESTED)

    if result.yes_no is True:
        return await _finish_collection(db, session)

    # Wrong or unclear -- drop it and either retry or move on.
    update_collected(session, email=None)
    if session.email_attempt_count >= settings.voice_max_email_attempts:
        return await _finish_collection(db, session, preamble=scripts.EMAIL_GIVE_UP)
    session.state = VoiceCallState.COLLECT_EMAIL
    return TurnOutcome(_speak(session, _ask(session, scripts.EMAIL_RETRY)))


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
        _set_name(session, changes["caller_name"], model_confidence=None)
        update_collected(session, name_confirmed_as=None)
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
            "department_verified", "phone_number", "phone_spoken", "phone_skipped", "email",
            "callback_confirmed", "callback_number", "callback_fallback",
            "ticket_ids",
        )
        session.collected = {k: session.collected.get(k) for k in keep if session.collected.get(k) is not None}
        session.collected["asked"] = ["department", "email"]
        session.collected["ticket_filed"] = False  # the next issue gets its own ticket
        session.email_attempt_count = 0
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
        line = scripts.LEAVING_WITH_TICKET.format(ticket_number=scripts.spoken_ticket_number(ticket.ticket_number))
        return TurnOutcome(_speak(session, reply.say_and_hangup(line)), ticket.id)

    session.state = VoiceCallState.ABANDONED
    return TurnOutcome(_speak(session, reply.say_and_hangup(scripts.LEAVING_NO_TICKET)))


# --- ticket creation ------------------------------------------------------


async def _create_ticket_and_read_back(
    db: AsyncSession, session: VoiceCallSession, *, preamble: str | None = None
) -> TurnOutcome:
    if issue_filed(session):
        # The gateway replayed the turn; this issue already has its ticket.
        ticket = await db.get(Ticket, session.ticket_id)
        read_back = scripts.READ_BACK.format(
            ticket_number=scripts.spoken_ticket_number(ticket.ticket_number)
        )
        return TurnOutcome(
            _speak(session, _say_then_ask(session, read_back, scripts.ANYTHING_ELSE)), session.ticket_id
        )

    ticket = await _create_ticket(db, session)
    _record_ticket(session, ticket)
    session.state = VoiceCallState.ANYTHING_ELSE

    lines = [preamble] if preamble else []
    if session.collected.get("summary_confirmed"):
        # The priority and its reason were already read back and confirmed.
        lines.append(scripts.SUMMARY_FILING)
    else:
        announcement = priority_rules.notice(_assessment(session))
        if announcement:
            lines.append(announcement)
        lines.append(scripts.CREATING_TICKET)
    lines.append(
        scripts.READ_BACK.format(ticket_number=scripts.spoken_ticket_number(ticket.ticket_number))
    )

    return TurnOutcome(
        _speak(session, _say_then_ask(session, " ".join(lines), scripts.ANYTHING_ELSE)), ticket.id
    )


async def _create_ticket(
    db: AsyncSession,
    session: VoiceCallSession,
    *,
    escalation_note: str | None = None,
    raise_priority: bool = True,
) -> Ticket:
    """Build a ticket from collected slots via the unchanged Phase 1 service.

    A ticket carrying an `escalation_note` is a callback request or salvaged
    call, and is raised to at least High so it doesn't wait in a normal queue
    (automation couldn't serve that caller). `raise_priority=False` keeps the
    assessed priority for a caller who simply chose to leave.
    """
    collected = session.collected

    description = collected.get("description") or ""
    if escalation_note:
        description = f"{escalation_note}\n\n{description}".strip()
    if collected.get("details"):
        description = f"{description}\n\n{DETAILS_MARKER}\n{collected['details']}".strip()
    details = _intake_details(collected)
    if details:
        description = f"{description}\n\n{INTAKE_MARKER}\n{details}".strip()
    description = f"{description}\n\n{TRANSCRIPT_MARKER}\n{transcript_text(session)}".strip()
    if collected.get("needs_triage"):
        description = f"{priority_rules.triage_note(_assessment(session))}\n\n{description}".strip()

    priority = Priority(collected.get("priority") or Priority.MEDIUM.value)
    if escalation_note and raise_priority:
        # Automation couldn't serve this caller; don't leave them in a normal queue.
        priority = Priority.URGENT if priority == Priority.URGENT else Priority.HIGH

    # The number to call: the one the caller confirmed, else the one we had (caller ID or spoken).
    callback_number = collected.get("callback_number")
    phone = callback_number or collected.get("phone_number") or (
        session.from_number if caller_id_is_usable(session.from_number) else "unknown"
    )
    # What the call came in from, kept as-is for auditing ("anonymous" says it was withheld).
    inbound = (session.from_number or "").strip()
    caller_number = None if inbound in ("", "unknown") else inbound[:32]
    payload = TicketCreate(
        caller_name=collected.get("caller_name") or "Unknown caller (voice)",
        phone_number=phone,
        email=collected.get("email"),
        category_id=await _category_id(db, collected.get("category")),
        priority=priority,
        description=description[:10_000],
    )

    # Simulated calls (VOICE_SIMULATOR_DESIGN.md) run this exact path; only
    # the source differs, which keeps their tickets out of the real queue.
    source = TicketSource.SIMULATOR if session.is_simulated else TicketSource.PHONE
    return await ticket_service.create_ticket(
        db, payload, source=source, caller_number=caller_number, callback_number=callback_number
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
    reason = collected.get("priority_reason")
    priority = collected.get("priority")
    priority_line = None
    if priority:
        priority_line = f"{priority.title()}" + (f": {reason}" if reason else "")

    lines = [
        ("Department", department),
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
    ]
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
    prompt = retries.get(session.state, scripts.DESCRIPTION_RETRY)[retry_index]
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
        return TurnOutcome(_speak(session, _ask(session, scripts.ESCALATION_PHONE_ASK)))

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

    if phone:
        opener = (
            scripts.ESCALATION_CALLER_REQUESTED
            if reason == EscalationReason.CALLER_REQUESTED
            else scripts.ESCALATION_MISUNDERSTOOD
        ).format(phone=scripts.spoken_phone_number(phone))
    elif reason == EscalationReason.CALLER_REQUESTED:
        # The caller was perfectly clear; never tell them we didn't understand.
        opener = scripts.ESCALATION_CALLER_REQUESTED_NO_NUMBER
    else:
        opener = scripts.ESCALATION_NO_CALLBACK_NUMBER

    read_back = scripts.ESCALATION_READ_BACK.format(
        ticket_number=scripts.spoken_ticket_number(ticket.ticket_number)
    )
    return TurnOutcome(
        _speak(session, reply.say_and_hangup(opener, read_back, scripts.GOODBYE)), ticket.id
    )


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
