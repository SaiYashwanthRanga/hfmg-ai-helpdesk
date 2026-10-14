"""Issue-specific diagnostic questions for employee IT calls: an intake agent, not a troubleshooter.

After the caller has described the problem (and said when it started and whether they can work), the agent
asks the few questions IT needs for that kind of problem -- which device, the exact error, which server and
drive -- one at a time, skipping anything already said. It never asks the caller to try anything (restart,
reconnect, sign in somewhere else), never claims to have looked at or fixed anything, and never asks for a
password or code. Answers are read with keywords in code and kept in the caller's own words; no model call is
made here (the problem's category and description already came from one).

Signs of a security incident, data loss, a failing service, or everyone being affected are flagged: only the
essential questions are still asked, the priority is raised, and the reason goes on the ticket. Nothing is
done about the problem on the call.

All state lives in the call's `collected["diagnostics"]` (plain JSON), so retries, replays and concurrent calls
behave as for every other step.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from app.db.models import Priority
from app.voice import nlu

MAX_QUESTIONS = 4

Values = dict[str, str | None]


@dataclass(frozen=True)
class Question:
    key: str
    label: str  # how the answer is named on the ticket
    prompt: str
    kind: str  # "choice" | "yes_no" | "text"
    choices: tuple[tuple[str, tuple[str, ...]], ...] = ()  # (value, keywords); first match wins
    when: Callable[[Values], bool] = field(default=lambda v: True)
    #: Still asked when the problem is urgent: what IT needs to act at all (which server, which drive).
    essential: bool = False
    #: yes_no only: words in the problem description that already answer "yes".
    prefill: tuple[str, ...] = ()


@dataclass(frozen=True)
class Playbook:
    name: str
    label: str
    questions: tuple[Question, ...]
    next_action: Callable[[Values], str]


_SCOPE = Question("scope", "Others affected", "Is anyone else having the same problem?", "yes_no")
_ERROR = Question("error", "Error message", "Do you see an error message? If so, what does it say?", "text")
_LEAD = re.compile(r"^(?:(?:um|uh|so|well|it'?s|it is|that'?s|that is|the|i think|probably|maybe|about)\b[\s,]*)+",
                   re.IGNORECASE)


def _said(values: Values, key: str, default: str = "") -> str:
    """The caller's answer, trimmed to fit inside a sentence for IT ("It's FS01." -> "FS01"). The ticket's own
    line for that question keeps their exact words."""
    value = values.get(key)
    if not value or value == "none" or value.startswith("("):
        return default
    brief = _LEAD.sub("", value).strip().rstrip(".!?, ")
    return brief or default


# --- playbooks ------------------------------------------------------------------------------------------------

OUTLOOK = Playbook(
    "outlook", "Outlook / email",
    questions=(
        Question("device", "Device", "Is this happening on your laptop, your phone, or both?", "choice",
                 (("both", ("both", "everywhere")), ("phone", ("phone", "mobile", "iphone", "android", "cell")),
                  ("computer", ("computer", "laptop", "desktop", "pc", "workstation")))),
        Question("symptom", "Symptom",
                 "What happens: does Outlook crash or not open, is it a sign-in problem, or are emails not sending or arriving?",
                 "choice",
                 (("sign_in", ("sign in", "sign-in", "signin", "log in", "login", "password", "credential", "keeps asking")),
                  ("send_receive", ("send", "receiv", "arriv", "outbox", "stuck", "not getting", "bounce")),
                  ("crash", ("crash", "close", "freez", "not open", "won't open", "wont open", "doesn't open",
                             "spin", "hang", "not responding", "stopped working")))),
        _ERROR,
        Question("mail_flow", "Can still send and receive email",
                 "Are you still able to send and receive email at all?", "yes_no",
                 when=lambda v: v.get("symptom") != "send_receive"),
        _SCOPE,
    ),
    next_action=lambda v: (
        "Check the caller's account sign-in (lockout, password expiry, MFA)." if v.get("symptom") == "sign_in" else
        "Check mail flow for the caller's mailbox (message trace, mailbox size and rules)."
        if v.get("symptom") == "send_receive" or v.get("mail_flow") == "no" else
        "Check the mail setup on the caller's phone." if v.get("device") == "phone" else
        "Check the Outlook app on the caller's computer (Office repair or a new Outlook profile)."
        if v.get("symptom") == "crash" else
        "Check the caller's Outlook and mailbox."
    ),
)

SERVER_STORAGE = Playbook(
    "server_storage", "Server / storage",
    questions=(
        Question("server", "Server", "Which server is it? The server name, if you know it.", "text", essential=True),
        Question("drive", "Drive or folder", "Which drive or shared folder is running out of space?", "text",
                 essential=True),
        Question("app_impact", "Application failing",
                 "Is any application or service already failing because of it?", "yes_no",
                 prefill=("failing", "fails", "failed", "can't save", "cannot save", "won't save", "crash", "is down",
                          "went down", "stopped working", "not working")),
        Question("free_space", "Free space now", "Do you know how much free space is left?", "text"),
        Question("space_needed", "Space needed", "How much more space do you think is needed?", "text"),
        _SCOPE,
    ),
    next_action=lambda v: (
        f"Check free space on {_said(v, 'server', 'the server')} ({_said(v, 'drive', 'drive not given')}) and extend "
        f"the volume or free up space. Space needed (caller's estimate): {_said(v, 'space_needed', 'not given')}."
    ),
)

NETWORK = Playbook(
    "network", "Network / VPN",
    questions=(
        Question("location", "Location", "Are you in the office or working remotely?", "choice",
                 (("remote", ("home", "remote", "remotely", "away", "off site", "offsite", "travel")),
                  ("office", ("in the office", "office", "at work", "clinic", "on site", "onsite", "building")))),
        Question("symptom", "Symptom",
                 "Is it no connection at all, slow, or just one site or system you can't reach?", "choice",
                 (("vpn", ("vpn",)),
                  ("specific", ("one site", "one system", "just one", "only one", "share", "drive", "server", "website")),
                  ("slow", ("slow", "lag", "drop", "keeps disconnecting", "intermittent")),
                  ("none", ("nothing", "no connection", "no internet", "not at all", "can't connect", "cannot connect",
                            "offline", "down")))),
        Question("resource", "System it can't reach", "Which site or system can't you reach?", "text",
                 when=lambda v: v.get("symptom") == "specific"),
        _ERROR,
        _SCOPE,
    ),
    next_action=lambda v: (
        "Several people are affected: check the network and VPN service for an outage first."
        if v.get("scope") == "yes" else
        "Check the caller's VPN client and VPN account (remote connection)."
        if v.get("location") == "remote" or v.get("symptom") == "vpn" else
        f"Check access to {_said(v, 'resource', 'the system they named')} from the caller's location."
        if v.get("symptom") == "specific" else
        "Check the caller's connection at their desk (port, cable, Wi-Fi)."
    ),
)

DEVICE = Playbook(
    "device", "Laptop / desktop",
    questions=(
        Question("device", "Device", "Is it a laptop or a desktop computer?", "choice",
                 (("laptop", ("laptop", "notebook", "surface")), ("desktop", ("desktop", "tower", "pc", "workstation")))),
        Question("symptom", "Symptom",
                 "What happens: does it not turn on, is it very slow or freezing, or is there an error on the screen?",
                 "choice",
                 (("no_power", ("turn on", "power", "dead", "won't start", "wont start", "black screen", "boot")),
                  ("slow", ("slow", "freez", "hang", "lag", "stuck", "not responding")),
                  ("error", ("error", "blue screen", "message", "warning")))),
        Question("error", "Error message", "What does the error on the screen say?", "text",
                 when=lambda v: v.get("symptom") == "error"),
        Question("recent_change", "Recent change",
                 "Did anything change recently, like an update or new software?", "yes_no"),
    ),
    next_action=lambda v: (
        "The device does not power on: arrange a hands-on check or a swap." if v.get("symptom") == "no_power" else
        "Check the computer remotely or in person; note the recent change the caller mentioned."
        if v.get("recent_change") == "yes" else
        "Check the computer remotely or in person."
    ),
)

ACCOUNT = Playbook(
    "account", "Account / access",
    questions=(
        Question("account", "Account or application",
                 "Which account or application can't you get into? Please don't tell me your password.", "text"),
        Question("error", "Error message", "What does the message say, exactly?", "text"),
        Question("worked_before", "Worked before", "Was it working for you before?", "yes_no"),
    ),
    next_action=lambda v: (
        f"Check the account status for {_said(v, 'account', 'the account')} (locked, expired, MFA) and the caller's "
        "access rights. The caller was not asked for, and should not be asked for, a password over the phone."
    ),
)

PRINTER = Playbook(
    "printer", "Printer / peripherals",
    questions=(
        Question("printer", "Device", "Which printer or device is it? The name or where it is.", "text"),
        Question("connection", "Connection",
                 "Is it connected by a cable to your computer, or is it a shared network printer?", "choice",
                 (("usb", ("cable", "usb", "plugged", "directly", "my computer")),
                  ("network", ("network", "shared", "wireless", "wifi", "wi-fi", "everyone", "hallway")))),
        Question("error", "Error shown", "Does it show an error, like a paper jam or offline?", "text"),
        _SCOPE,
    ),
    next_action=lambda v: (
        f"Check the device ({_said(v, 'printer', 'not named')}): power, error state and print queue"
        + ("; several people can't use it." if v.get("scope") == "yes" else ".")
    ),
)

OTHER = Playbook(
    "other", "Other",
    questions=(
        Question("what_happens", "What happens", "What exactly happens when you try?", "text"),
        _ERROR,
        _SCOPE,
    ),
    next_action=lambda v: "Call the caller back to diagnose; see what they described.",
)

PLAYBOOKS = {p.name: p for p in (OUTLOOK, SERVER_STORAGE, NETWORK, DEVICE, ACCOUNT, PRINTER, OTHER)}

_SERVER_WORDS = ("disk space", "out of space", "running out of space", "storage", "drive is full", "drive full",
                 "no space", "space left", "server")
_EMAIL_WORDS = ("email", "e-mail", "mail", "inbox", "calendar")
_PRINTER_WORDS = ("printer", "print", "scanner", "scan", "toner", "keyboard", "mouse", "headset", "monitor",
                  "webcam", "docking", "dock")
_NETWORK_WORDS = ("vpn", "wifi", "wi-fi", "internet", "network", "connection", "connect")
_ACCOUNT_WORDS = ("password", "locked out", "lock out", "log in", "login", "sign in", "signin", "access", "account",
                  "permission", "mfa", "authenticator")
_DEVICE_WORDS = ("laptop", "computer", "desktop", "pc", "screen", "turn on", "blue screen", "slow", "freez")


def choose(category: str | None, description: str | None) -> Playbook:
    """The question set for the problem as described. Server and Outlook words win over the broad categories."""
    text = (description or "").lower()
    if any(w in text for w in _SERVER_WORDS):
        return SERVER_STORAGE
    if "outlook" in text or (category == "Microsoft 365" and any(w in text for w in _EMAIL_WORDS)):
        return OUTLOOK
    if category == "Printer" or any(w in text for w in _PRINTER_WORDS):
        return PRINTER
    if category == "Network" or any(w in text for w in _NETWORK_WORDS):
        return NETWORK
    if category == "Password" or any(w in text for w in _ACCOUNT_WORDS):
        return ACCOUNT
    if any(w in text for w in _DEVICE_WORDS):
        return DEVICE
    return OTHER


# --- reading answers ------------------------------------------------------------------------------------------

# "my password is X", "code: X", "PIN = X": the value after an explicit "is", "was", ":" or "=" is removed.
_SECRET = re.compile(r"\b(password|passcode|pin|code)(?:\s+(?:is|was)\s*[:=]?|\s*[:=])\s*\S+", re.IGNORECASE)
_DECLINE = re.compile(r"^\W*(no|nope|none|nothing|no error|no message|not really|i don'?t know|not sure|n/?a)\b",
                      re.IGNORECASE)
_SECURITY = ("hacked", "phish", "ransom", "virus", "malware", "suspicious email", "clicked a link", "clicked on a link",
             "compromis", "breach", "someone else logged", "strange login", "unknown login")
_DATA_LOSS = ("lost all", "data loss", "files are gone", "files are missing", "missing files", "got deleted",
              "were deleted", "was deleted", "corrupt")
_EVERYONE = ("everyone", "everybody", "whole office", "whole site", "whole clinic", "all of us", "nobody can",
             "no one can", "the entire")

URGENT_NOTICE = "This needs our IT team right away, so I'll get it logged now."


def clean(utterance: str) -> str:
    """The caller's words as kept on the ticket: trimmed, and with anything that looks like a secret removed."""
    return _SECRET.sub(lambda m: f"{m.group(1)} [removed]", " ".join((utterance or "").split()))[:240]


def _parse(question: Question, utterance: str) -> str | None:
    text = (utterance or "").lower()
    if question.kind == "yes_no":
        answer = nlu.quick_yes_no(utterance)
        return None if answer is None else ("yes" if answer else "no")
    if question.kind == "choice":
        return next((value for value, words in question.choices if any(w in text for w in words)), None)
    if not text.strip():
        return None
    return "none" if _DECLINE.match(text) and len(text.split()) <= 5 else clean(utterance)


def begin(category: str | None, description: str | None, affected_scope: str | None) -> dict:
    """Pick the question set and fill in what the description (or earlier answers) already say."""
    playbook = choose(category, description)
    values: Values = {}
    text = (description or "").lower()
    for question in playbook.questions:
        found = _parse(question, description or "") if question.kind == "choice" else None
        if question.kind == "yes_no" and any(w in text for w in question.prefill):
            found = "yes"
        if found:
            values[question.key] = found
            values[f"{question.key}_said"] = "(from the problem description)"
    if affected_scope in ("one_person", "several_people", "whole_site") and any(q.key == "scope" for q in playbook.questions):
        values["scope"] = "no" if affected_scope == "one_person" else "yes"
        values["scope_said"] = "(from earlier in the call)"
    state = {"playbook": playbook.name, "values": values, "asked": [], "pending": None, "done": False, "flags": []}
    _flag(state, description or "")
    return state


def _flag(state: dict, utterance: str) -> None:
    text = (utterance or "").lower()
    flags = set(state.get("flags") or [])
    if any(w in text for w in _SECURITY):
        flags.add("security")
    if any(w in text for w in _DATA_LOSS):
        flags.add("data_loss")
    if any(w in text for w in _EVERYONE):
        flags.add("everyone")
    state["flags"] = sorted(flags)


def urgent_reason(state: dict | None) -> str | None:
    """Why the remaining questions should be cut short and the ticket go in now, or None."""
    if not state:
        return None
    flags, values = set(state.get("flags") or []), state.get("values") or {}
    if "security" in flags:
        return "it may be a security incident"
    if "data_loss" in flags:
        return "data may have been lost"
    if values.get("app_impact") == "yes":
        return "an application is already failing"
    if "everyone" in flags:
        return "it's affecting everyone there"
    return None


def priority_floor(state: dict | None) -> tuple[Priority, str] | None:
    """The lowest priority the findings allow, with the spoken reason."""
    reason = urgent_reason(state)
    if reason is None:
        return None
    critical = reason in ("it may be a security incident", "it's affecting everyone there")
    return (Priority.URGENT if critical else Priority.HIGH), reason


def next_prompt(state: dict) -> tuple[str, str] | None:
    """(question key, what to say) for the next question, or None when the questions are over.

    An urgent problem gets only the essential questions (what IT needs to act)."""
    if state.get("done"):
        return None
    urgent = urgent_reason(state) is not None
    playbook = PLAYBOOKS[state["playbook"]]
    values = state["values"]
    if len(state["asked"]) >= MAX_QUESTIONS:
        return None
    for question in playbook.questions:
        if urgent and not question.essential:
            continue
        if question.key not in values and question.key not in state["asked"] and question.when(values):
            return question.key, question.prompt
    return None


def record(state: dict, utterance: str) -> tuple[dict, dict]:
    """Apply the caller's answer to the pending question. Returns (new state, updates for `collected`)."""
    state = {**state, "values": dict(state["values"]), "asked": list(state["asked"])}
    key = state.get("pending")
    _flag(state, utterance)
    updates: dict = {}
    question = next((q for q in PLAYBOOKS[state["playbook"]].questions if q.key == key), None)
    if question is not None:
        state["asked"].append(question.key)
        value = _parse(question, utterance)
        if value is not None:
            state["values"][question.key] = value
        if utterance.strip():
            state["values"][f"{question.key}_said"] = clean(utterance)
        if question.key == "scope" and value == "yes":
            updates["affected_scope"] = "whole_site" if "everyone" in state["flags"] else "several_people"
        elif question.key == "scope" and value == "no":
            updates["affected_scope"] = "one_person"
    state["pending"] = None
    return state, updates


def ticket_lines(state: dict | None) -> list[tuple[str, str]]:
    """The diagnostics section of the ticket's intake details: each answer in the caller's words, what is still
    missing, the escalation reason, and a suggested next step for IT."""
    if not state:
        return []
    playbook = PLAYBOOKS[state["playbook"]]
    values = state.get("values") or {}
    lines: list[tuple[str, str]] = [("Issue type", playbook.label)]
    missing = []
    for question in playbook.questions:
        said = values.get(f"{question.key}_said")
        value = values.get(question.key)
        if value is None and said is None:
            if question.when(values):
                missing.append(question.label.lower())
            continue
        shown = said if said and not said.startswith("(") else value
        if said and said.startswith("("):
            shown = f"{value} {said}"
        if question.kind == "yes_no" and value in ("yes", "no") and said and not said.startswith("("):
            shown = f"{value} (caller: \"{said}\")"
        lines.append((question.label, shown or "unclear"))
    reason = urgent_reason(state)
    if reason:
        lines.append(("Escalation", f"flagged urgent: {reason}"))
    if missing:
        lines.append(("Not established", ", ".join(missing)))
    lines.append(("Suggested next step", playbook.next_action(values)))
    return lines
