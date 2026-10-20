"""Context-aware employee conversations: the acceptance tests from "HFMG AI Helpdesk -- Intelligent Conversation
Behavior Test File", by TC number.

The agent understands first, asks only what is still missing and useful, adapts to the caller's answers, and files
an accurate ticket without making the caller repeat themselves. Full dialogues through the real /start and /turn
routes; the language model is stubbed for the opening description and the name only. Every other step is rules.
Synthetic callers only.

Already covered elsewhere (and still run): caller-type corrections at every stage (TC-022, 029, 046, 047 --
test_patient_edge_cases, test_caller_routing), human callback and goodbye (TC-024/025 -- test_escalation_wording),
bounded retries and no invented facts (TC-032/034), the persistence guard and retries (TC-050/051 --
test_patient_edge_cases, test_turn_idempotency), model failure fallbacks (TC-052 -- test_voice_agent), patient
priority and separation (TC-039, 042-045).
"""

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import Category, Priority, Ticket, VoiceCallState
from app.voice import diagnostics as dx
from app.voice import nlu, priority as priority_rules, scripts
from tests.test_directory_identity import DirCall
from tests.test_multi_issue_calls import _session, _ticket
from tests.test_multi_issue_calls import call as _call_fixture  # noqa: F401
from tests.test_ticket_followup import sent_emails  # noqa: F401

pytestmark = pytest.mark.asyncio(loop_scope="session")
settings = get_settings()
CALLER = "+18455550399"


@pytest.fixture(autouse=True)
async def _production_flow(monkeypatch, db_session):
    for name, value in (("voice_ask_caller_type", True), ("voice_diagnostic_questions", True),
                        ("voice_ask_extension", True), ("voice_confirm_summary", True)):
        monkeypatch.setattr(settings, name, value)
    for name in ("Microsoft 365", "Network", "Printer", "Password", "Other"):
        if (await db_session.execute(select(Category).where(Category.name == name))).scalar_one_or_none() is None:
            db_session.add(Category(name=name, default_priority=Priority.MEDIUM))
    await db_session.commit()


@pytest.fixture
def make_call(client, monkeypatch, _call_fixture):  # noqa: F811
    def make(call_id):
        return DirCall(client, monkeypatch, call_id, CALLER)

    return make


def _text(outcome) -> str:
    return " ".join(outcome["lines"])


async def _describe(call, said, category="Other", *, short="your computer", blocked=False, started="this morning",
                    priority=Priority.MEDIUM, scope=None):
    await call.start()
    await call.turn("I'm an employee.")
    call.classify_as("INTERNAL_IT", 0.95)
    call._say(interpret_description=nlu.TurnResult(
        value=said, confidence="high", unable_to_determine=False, category=category, priority=priority,
        extras={"short_issue": short, "started": started, "work_blocked": blocked, "affected_scope": scope},
    ))
    return await call.turn(said)


def _asks_name(outcome) -> bool:
    return any(option in _text(outcome) for option in scripts.NAME_ASK_OPTIONS)


async def _name(call, name="Pat Real", department="Billing"):
    call._say(interpret_name=nlu.TurnResult(value=name, confidence="high", unable_to_determine=False,
                                            extras={"department": department, "name_confidence": "high"}))
    return await call.turn(f"{name}, {department}")


async def _file(call):
    """Name, no extension, skip the email, yes to the read-back."""
    out = await _name(call)
    for said in ("No, I don't have one.", "skip", "Yes."):
        if out["ticket_id"] or not out["expect_reply"]:
            break
        out = await call.turn(said)
    return out


# --- 2. dynamic question planning --------------------------------------------------------------------------------


async def test_tc001_outlook_crash_asks_a_missing_detail_never_what_was_said(make_call, db_session):
    call = make_call("ic-001")

    out = await _describe(call, "My Outlook keeps crashing when I open it.", "Microsoft 365", short="your Outlook")

    assert _text(out).endswith("Is this happening on your laptop, your phone, or both?")
    assert "crash or not open" not in _text(out)  # the symptom was said


async def test_tc002_a_caller_who_gives_many_facts_is_asked_only_what_is_missing(make_call, client, db_session, sent_emails):
    call = make_call("ic-002")
    said = "Since this morning, Outlook crashes on my laptop, but I can still send emails from my phone."

    out = await _describe(call, said, "Microsoft 365", short="your Outlook")

    assert _text(out).endswith(dx._ERROR.prompt)  # application, symptom, start, device, workaround: all known
    values = (await _session(db_session, "ic-002")).collected["diagnostics"]["values"]
    assert (values["device"], values["symptom"], values["mail_flow"]) == ("computer", "crash", "yes")
    assert "phone" in values["workaround"]
    assert _asks_name(await call.turn("No error, it just closes."))  # enough: on to the paperwork
    filed = await _file(call)
    intake = (await _ticket(client, filed["ticket_id"]))["description"].split("--- Call transcript ---")[0]
    for expected in ("Device: computer", "Symptom: crash", "Started: this morning", "Work blocked: no", "Workaround:"):
        assert expected in intake, expected


async def test_tc003_a_quoted_error_is_not_asked_for_again(make_call, client, db_session, sent_emails):
    call = make_call("ic-003")

    out = await _describe(call, "Outlook won't open on my laptop, it says 'Cannot start Microsoft Outlook'.",
                          "Microsoft 365", short="your Outlook")

    assert "error message" not in _text(out).lower()
    filed = await _file(call)
    intake = (await _ticket(client, filed["ticket_id"]))["description"]
    assert "Error message: it says 'Cannot start Microsoft Outlook'" in intake  # TC-014: the caller's own words


@pytest.mark.parametrize("call_id", ["ic-019"])
async def test_tc004_tc019_enough_information_goes_straight_to_the_paperwork(make_call, client, db_session, sent_emails, call_id):
    call = make_call(call_id)

    out = await _describe(call, "My keyboard stopped working after I spilled water on it. I have a spare keyboard, so I "
                                "can continue working.", short="your keyboard")

    assert _asks_name(out)  # no diagnostic question at all
    filed = await _file(call)
    intake = (await _ticket(client, filed["ticket_id"]))["description"]
    assert "Likely cause (caller): spilled water on it" in intake and "Workaround: I have a spare keyboard" in intake


async def test_tc005_the_question_budget_is_a_ceiling():
    state = dx.begin("Other", "Something odd is happening.", None)
    asked = 0
    while (found := dx.next_prompt(state)) is not None:
        state["pending"] = found[0]
        state, _, _ = dx.record(state, "I'm not sure.")
        asked += 1
    assert asked <= dx.MAX_QUESTIONS


async def test_tc006_tc009_an_unknown_answer_is_recorded_and_never_asked_again():
    state = dx.begin("Other", f"The server 172.22.6.98 is not connecting.", None)
    state["pending"] = dx.next_prompt(state)[0]  # its name

    state, _, reask = dx.record(state, "I don't know.")

    assert reask is None and state["values"]["server_name"] == "unknown"
    assert dx.next_prompt(state)[0] != "server_name"


# --- 3/4. state, and understanding the problem -------------------------------------------------------------------


async def test_tc008_an_inferred_fact_is_never_shown_as_said():
    state = dx.begin("Microsoft 365", "Outlook crashes when I open it. I can still work using my phone.", None,
                     said="Outlook crashes when I open it. I can still work using my phone.")
    assert state["values"]["device"] == "computer" and state["values"]["device_said"].startswith("(inferred")


@pytest.mark.parametrize("said, playbook, symptom", [
    ("Outlook keeps crashing.", "outlook", "crash"),
    ("Outlook is asking me to sign in repeatedly.", "outlook", "sign_in"),
    ("Outlook is working, but I need help setting up a new mailbox.", "service_request", None),
])
async def test_tc011_the_same_application_with_different_problems(said, playbook, symptom):
    state = dx.begin("Microsoft 365", said, None, said=said)
    assert state["playbook"] == playbook and state["values"].get("symptom") == symptom


async def test_tc011_a_service_request_is_not_troubleshot():
    said = "Outlook is working, but I need help setting up a new mailbox."
    state = dx.begin("Microsoft 365", said, None, said=said)
    assert dx.next_prompt(state) is None  # nothing broken to ask about
    assert "Service request" in dict(dx.ticket_lines(state))["Suggested next step"]


async def test_tc012_a_slow_server_is_not_a_server_that_is_down():
    slow = dx.begin("Other", "The server 10.0.0.5 is really slow today.", None)
    down = dx.begin("Other", "The server 10.0.0.5 is down.", None)
    assert (slow["values"]["server_state"], down["values"]["server_state"]) == ("slow", "down")
    slow["values"]["server_name"] = down["values"]["server_name"] = "unknown"
    slow["values"]["resource_type"] = down["values"]["resource_type"] = "unknown"
    assert dx.next_prompt(slow)[0] == "slowness" and dx.next_prompt(down)[0] == "error"
    assert "slow" in dict(dx.ticket_lines(slow))["Suggested next step"]


@pytest.mark.parametrize("said, playbook", [
    ("The printer works fine but my keyboard doesn't.", "peripheral"),
    ("My keyboard is fine, the printer is jammed.", "printer"),
    ("My monitor works but the VPN keeps dropping.", "network"),
])
async def test_tc013_the_failing_device_decides_not_the_first_keyword(said, playbook):
    assert dx.choose("Other", said).name == playbook


# --- 5. adaptive follow-ups ----------------------------------------------------------------------------------------


async def test_tc015_scenario_a_outlook_asks_only_the_error(make_call, db_session):
    call = make_call("ic-015")

    out = await _describe(call, "Outlook crashes when I open it. It started this morning, and I can still work using my "
                                "phone.", "Microsoft 365", short="your Outlook")

    assert _text(out).endswith(dx._ERROR.prompt)


async def test_tc016_scenario_b_vpn_does_not_ask_location_or_others(make_call, db_session):
    call = make_call("ic-016")

    out = await _describe(call, "The VPN disconnects every few minutes when I'm working from home. Nobody else has "
                                "reported it.", "Network", short="the VPN")

    assert _text(out).endswith(dx._ERROR.prompt)
    values = (await _session(db_session, "ic-016")).collected["diagnostics"]["values"]
    assert (values["location"], values["vpn_kind"], values["scope"]) == ("remote", "drops", "no")


async def test_tc017_tc018_an_urgent_problem_is_flagged_first_then_only_what_is_essential(make_call, client, db_session, sent_emails):
    call = make_call("ic-017")

    first = await _describe(call, "The file server is out of space, and the application that uses it has stopped working.",
                            short="the file server", blocked=True)
    drive = await call.turn("FS01")
    name_q = await call.turn("The E drive.")
    filed = await _name(call)

    assert _text(first).endswith(f"{dx.urgent_notice({'values': {'app_impact': 'yes'}})} "
                                 "Which server is it? The server name, if you know it.")
    assert "Which drive" in _text(drive) and _asks_name(name_q)
    # Name and callback only: no extension, no email, no read-back -- filed at once.
    assert filed["ticket_id"] and "extension" not in _text(filed).lower() and "email" not in _text(filed).lower()
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["priority"] in ("HIGH", "URGENT")
    assert "Read-back: not done: urgent, filed straight away" in ticket["description"]


async def test_tc030_tc031_an_ambiguous_vpn_problem_gets_one_targeted_question(make_call, db_session):
    call = make_call("ic-030")

    out = await _describe(call, "My VPN isn't working at home.", "Network", short="the VPN")

    assert _text(out).endswith("Does the VPN connect and then drop, or can't it connect at all?")


# --- 6. corrections and interruptions -----------------------------------------------------------------------------


async def test_tc020_tc028_a_corrected_device_replaces_the_old_one_and_the_question_resumes(make_call, client, db_session, sent_emails):
    call = make_call("ic-020")
    asked = await _describe(call, "My laptop is very slow.", short="your laptop")
    assert _text(asked).endswith("Did anything change recently, like an update or new software?")

    out = await call.turn("Sorry, I meant my desktop, not my laptop.")

    assert _text(out) == f"{dx.CORRECTED} Did anything change recently, like an update or new software?"
    values = (await _session(db_session, "ic-020")).collected["diagnostics"]["values"]
    assert (values["device"], values["device_corrected_from"]) == ("desktop", "laptop")


async def test_tc021_tc038_others_affected_said_later_widens_the_scope_and_reassesses_priority(make_call, db_session):
    call = make_call("ic-021")
    await _describe(call, "My Outlook won't open.", "Microsoft 365", short="your Outlook", blocked=True)
    # The caller cannot work, so the impact questions are asked: answered, including "just me" for now.
    for said in ("My laptop.", "No error.", "No, I can't send any email.", "Just me, I think."):
        await call.turn(said)
    await _name(call)
    before = (await _session(db_session, "ic-021")).collected["priority"]

    out = await call.turn("Oh, and the Billing team has the same issue.")  # at the extension question

    assert _text(out) == f"{scripts.OTHERS_NOTED} {scripts.EXTENSION_ASK}"
    c = (await _session(db_session, "ic-021")).collected
    assert c["affected_scope"] == "several_people" and c["diagnostics"]["values"]["scope"] == "yes"
    assert priority_rules.order(Priority(c["priority"])) >= priority_rules.order(Priority(before))
    assert c["priority_reason"] == "your team can't work"


async def test_tc021_nobody_else_never_widens_the_scope(make_call, db_session):
    call = make_call("ic-021-no")
    await _describe(call, "My Outlook won't open.", "Microsoft 365", short="your Outlook")
    for said in ("My laptop.", "No error."):
        await call.turn(said)
    await _name(call)

    await call.turn("No one else on the team has the same issue, just me. No extension.")

    assert (await _session(db_session, "ic-021-no")).collected.get("affected_scope") != "several_people"


async def test_tc023_not_the_printer_the_scanner_corrects_the_device_and_carries_on(make_call, db_session):
    call = make_call("ic-023")
    asked = await _describe(call, "The printer by reception is jammed.", "Printer", short="the printer")
    assert _text(asked).endswith("Which printer or device is it? The name or where it is.")

    out = await call.turn("It's not the printer, it's the scanner.")  # in answer to "which device?"

    assert _text(out) == ("Thanks, the scanner. Is it connected by a cable to your computer, or is it a shared network "
                          "printer?")
    c = (await _session(db_session, "ic-023")).collected
    assert c["short_issue"] == "the scanner" and c["diagnostics"]["values"]["printer"] == "scanner"


async def test_tc026_a_new_problem_after_filing_gets_its_own_ticket(make_call, client, db_session, sent_emails):
    call = make_call("ic-026")
    await _describe(call, "My keyboard stopped working after I spilled water on it, I have a spare.", short="your keyboard")
    first = await _file(call)

    asked = await call.turn("My printer is jammed as well though.")
    call._say(interpret_description=nlu.TurnResult(
        value="The printer is jammed.", confidence="high", unable_to_determine=False, category="Printer",
        priority=Priority.MEDIUM, extras={"short_issue": "the printer", "started": "today", "work_blocked": False},
    ))
    await call.turn("A new one.")

    assert _text(asked) == scripts.SAME_OR_NEW_ASK
    one = await _ticket(client, first["ticket_id"])
    assert "printer" not in one["description"].split("--- Call transcript ---")[0].lower()  # never mixed in
    assert (await _session(db_session, "ic-026")).collected["description"] == "The printer is jammed."


async def test_tc027_more_about_the_same_problem_is_added_to_its_ticket_never_a_duplicate(make_call, client, db_session, sent_emails):
    call = make_call("ic-027")
    await _describe(call, "My keyboard stopped working after I spilled water on it, I have a spare.", short="your keyboard")
    first = await _file(call)

    asked = await call.turn("Oh, it also beeps when I plug it in.")
    added = await call.turn("The same problem.")

    assert _text(asked) == scripts.SAME_OR_NEW_ASK
    ticket = await _ticket(client, first["ticket_id"])
    assert "Added by the caller after filing: Oh, it also beeps when I plug it in." in ticket["description"]
    assert scripts.SAME_ISSUE_ADDED.format(ticket_number=scripts.spoken_ticket_number(ticket["ticket_number"])) in _text(added)
    assert len((await db_session.execute(select(Ticket))).scalars().all()) == 1


async def test_a_plain_no_at_anything_else_still_ends_the_call(make_call, db_session, sent_emails):
    call = make_call("ic-bye")
    await _describe(call, "My keyboard stopped working after I spilled water on it, I have a spare.", short="your keyboard")
    await _file(call)

    out = await call.turn("No, that's all.")

    assert out["expect_reply"] is False and scripts.GOODBYE in _text(out)


# --- 8. urgency and priority ---------------------------------------------------------------------------------------


async def test_tc035_a_single_user_issue_with_a_workaround_is_not_an_outage(make_call, db_session):
    call = make_call("ic-035")
    await _describe(call, "Outlook keeps crashing on my laptop but it works on my phone.", "Microsoft 365",
                    short="your Outlook")
    assert (await _session(db_session, "ic-035")).collected["priority"] in ("LOW", "MEDIUM")


async def test_tc036_everyone_affected_is_urgent(make_call, db_session):
    call = make_call("ic-036")
    await _describe(call, "Nobody can print, the whole office printer is down.", "Printer", short="the printer",
                    blocked=True, scope="whole_site")
    assert (await _session(db_session, "ic-036")).collected["priority"] == "URGENT"


async def test_tc041_the_model_cannot_raise_a_single_user_issue_with_a_workaround(make_call, db_session):
    call = make_call("ic-041")
    await _describe(call, "Outlook keeps crashing but I can still work on my phone.", "Microsoft 365",
                    short="your Outlook", priority=Priority.URGENT)  # the model's reading says urgent
    assert (await _session(db_session, "ic-041")).collected["priority"] != "URGENT"


# --- found replaying the six scenarios against the real model ----------------------------------------------------


async def test_a_corrected_server_name_replaces_the_old_one_everywhere(make_call, client, db_session, sent_emails):
    call = make_call("ic-six-3")
    asked = await _describe(call, "The server FS01 is not connecting since this morning and I can't work.",
                            short="the server FS01", blocked=True)
    assert "hosts a database" in _text(asked)

    out = await call.turn("Sorry, the server name is actually FS02, not FS01.")

    assert _text(out).startswith(dx.CORRECTED) and "hosts a database" in _text(out)  # the open question again
    c = (await _session(db_session, "ic-six-3")).collected
    values = c["diagnostics"]["values"]
    assert (values["server"], values["server_corrected_from"]) == ("FS02", "FS01")
    assert c["short_issue"] == "the server FS02" and "Corrected by the caller: FS02, not FS01" in c["description"]


async def test_another_address_said_with_just_actually_is_asked_about_not_assumed():
    state = dx.begin("Other", "The server 172.22.6.98 is not connecting.", None)
    assert dx.find_correction(state, "Actually the server address is 172.22.6.50.") is None
    assert dx.find_correction(state, "Sorry, I meant 172.22.6.50, not 172.22.6.98.") == ("server", "172.22.6.50")


async def test_a_detail_corrected_after_the_questions_is_applied_and_the_rest_still_answers(make_call, client, db_session, sent_emails):
    call = make_call("ic-six-4")
    await _describe(call, "My laptop is very slow since this morning but I can still work.", short="your laptop's slowness")
    await call.turn("No, nothing changed.")
    asked = await _name(call)
    assert _text(asked) == scripts.EXTENSION_ASK

    out = await call.turn("Actually it's my desktop, not my laptop. And no extension.")

    assert _text(out).startswith(f"{dx.CORRECTED} {scripts.EXTENSION_NONE}")  # corrected, and "no extension" taken
    c = (await _session(db_session, "ic-six-4")).collected
    assert c["diagnostics"]["values"]["device"] == "desktop" and c["extension_source"] == "none"
    assert c["short_issue"] == "your desktop's slowness"


async def test_a_correction_with_nothing_else_asks_the_open_question_again(make_call, db_session, sent_emails):
    call = make_call("ic-six-4b")
    await _describe(call, "My laptop is very slow since this morning but I can still work.", short="your laptop")
    await call.turn("No, nothing changed.")
    await _name(call)

    out = await call.turn("Sorry, I meant my desktop, not my laptop.")

    assert _text(out) == f"{dx.CORRECTED} {scripts.EXTENSION_ASK}"


async def test_nobody_in_the_clinic_can_is_everyone_and_the_start_question_is_skipped(make_call, client, db_session, sent_emails):
    call = make_call("ic-six-5")

    first = await _describe(call, "The main file server FS01 is down and nobody in the clinic can open patient files.",
                            short="the main file server FS01", blocked=True, started=None)

    assert "urgent attention" in _text(first) and "When did this start" not in _text(first)
    filed = await _name(call)
    assert filed["ticket_id"]  # name and callback number only
    ticket = await _ticket(client, filed["ticket_id"])
    assert ticket["priority"] == "URGENT" and "Read-back: not done: urgent" in ticket["description"]


@pytest.mark.parametrize("said, urgent", [
    ("Nobody in the clinic can open patient files.", True), ("None of us can log in.", True),
    ("The whole office is offline.", True), ("Nobody else has this problem.", False),
    ("The whole team can't reach it.", False),  # a team is several people, not everyone
])
async def test_what_sounds_urgent(said, urgent):
    assert dx.sounds_urgent(said) is urgent


async def test_a_detail_after_filing_is_caught_even_when_the_model_hears_no(make_call, client, db_session, sent_emails):
    """With the real model, "Oh, it also beeps..." was read as "no" and the call ended with the detail lost."""
    call = make_call("ic-six-6")
    await _describe(call, "My keyboard stopped working after I spilled coffee on it, I'm using a spare one.",
                    short="your keyboard")
    first = await _file(call)
    call._say(interpret_yes_no=nlu.TurnResult(yes_no=False, unable_to_determine=False))  # the model's misreading

    asked = await call.turn("Oh, it also makes a beeping sound when I plug it in.")

    assert _text(asked) == scripts.SAME_OR_NEW_ASK and asked["expect_reply"] is True
    await call.turn("The same problem.")
    assert "beeping sound" in (await _ticket(client, first["ticket_id"]))["description"]
    assert len(sent_emails) == 1  # the email process is unchanged: no extra email for the note
