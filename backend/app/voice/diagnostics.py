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
    kind: str  # "choice" | "yes_no" | "text" | "host" (a server name, read out of whatever else was said)
    choices: tuple[tuple[str, tuple[str, ...]], ...] = ()  # (value, keywords); first match wins
    when: Callable[[Values], bool] = field(default=lambda v: True)
    #: Still asked when the problem is urgent: what IT needs to act at all (which server, which drive).
    essential: bool = False
    #: yes_no only: words in the problem description that already answer "yes".
    prefill: tuple[str, ...] = ()
    #: ...unless the description also has one of these (then the question is asked instead).
    prefill_unless: tuple[str, ...] = ()
    #: text only: a pattern whose match in the problem description already answers it ("the DC drive").
    prefill_pattern: str | None = None
    #: text only: words that restate the problem without naming what is asked for. An answer made only of these
    #: (and filler) is asked about once more with `clarify`, then recorded as not identified, never as the answer.
    vague: frozenset[str] = frozenset()
    clarify: str | None = None


@dataclass(frozen=True)
class Playbook:
    name: str
    label: str
    questions: tuple[Question, ...]
    next_action: Callable[[Values], str]
    #: What the caller's problem is NOT, said back after a correction: "..., not a printer". Blank: not said.
    noun: str = ""


_SCOPE = Question("scope", "Others affected", "Is anyone else having the same problem?", "yes_no")
_ERROR = Question("error", "Error message", "Do you see an error message? If so, what does it say?", "text")
_LEAD = re.compile(r"^(?:(?:um|uh|so|well|it'?s|it is|that'?s|that is|the|i think|probably|maybe|about)\b[\s,]*)+",
                   re.IGNORECASE)


def _servers(values: Values) -> str:
    """The server, plus any others the caller said are also affected."""
    names = [_said(values, "server", "the server")] + [n for n in (values.get("additional_servers") or "").split(", ") if n]
    return " and ".join(names)


def _said(values: Values, key: str, default: str = "") -> str:
    """The caller's answer, trimmed to fit inside a sentence for IT ("It's FS01." -> "FS01"). The ticket's own
    line for that question keeps their exact words."""
    value = values.get(key)
    if not value or value in ("none", "unknown") or value.startswith("("):
        return default
    brief = _LEAD.sub("", value).strip().rstrip(".!?, ")
    return brief or default


# --- playbooks ------------------------------------------------------------------------------------------------

OUTLOOK = Playbook(
    "outlook", "Outlook / email", noun="Outlook or email",
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
    "server_storage", "Server / storage", noun="server storage",
    questions=(
        Question("server", "Server", "Which server is it? The server name, if you know it.", "host", essential=True),
        Question("drive", "Drive or folder", "Which drive or shared folder is running out of space?", "text",
                 essential=True),
        Question("app_impact", "Application failing",
                 "Is any application or service already failing because of it?", "yes_no",
                 # Only an application that is failing now. "I can't install new software" (existing ones still
                 # run) is not one: the question is asked instead.
                 prefill=("app is failing", "application is failing", "apps are failing", "failing to save",
                          "can't save", "cannot save", "won't save", "crashed", "crashing", "is down", "went down",
                          "stopped working"),
                 prefill_unless=("install",)),
        Question("free_space", "Free space now", "Do you know how much free space is left?", "text"),
        Question("space_needed", "Space needed", "How much more space do you think is needed?", "text"),
        _SCOPE,
    ),
    next_action=lambda v: (
        f"Check free space on {_servers(v)} ({_said(v, 'drive', 'drive not given')}) and extend "
        f"the volume or free up space. Space needed (caller's estimate): {_said(v, 'space_needed', 'not given')}."
    ),
)

SERVER_ACCESS = Playbook(
    "server_access", "Server / system access", noun="a server",
    questions=(
        Question("server", "Server", "Which server is it? The server name or address, if you know it.", "host",
                 essential=True),
        Question("server_name", "Server name", "Do you know the server's name? I have the address {server}.", "host",
                 when=lambda v: is_ip(v.get("server")) and not v.get("server_name_resolved")),
        # What the server hosts decides the next questions; an IP address alone says nothing about it, so nothing is
        # assumed. Seen on a test call: a drive/share question for a server nobody said had a share.
        Question("resource_type", "What the server hosts",
                 "Do you know whether this server hosts a database, an application, a shared drive or folder, or "
                 "another service?", "choice",
                 (("database", ("database", "db", "sql", "oracle", "postgres", "mysql")),
                  ("file_share", ("share", "shared drive", "folder", "drive", "file server", "files")),
                  ("application", ("application", "app", "website", "web site", "portal", "software", "program",
                                   "service")))),
        Question("resource", "Drive, share or system", "Which shared drive or folder are you trying to reach?", "text",
                 prefill_pattern=r"\b(?:the\s+)?[A-Za-z0-9]{1,12}\s+(?:drive|share|folder)\b|\\\\[^\s]+",
                 when=lambda v: v.get("resource_type") == "file_share"),
        Question("application", "Application", "Which application or service is it?", "text",
                 when=lambda v: v.get("resource_type") == "application"),
        Question("db_client", "Application using the database", "Which application or service connects to the database?",
                 "text", when=lambda v: v.get("resource_type") == "database"),
        Question("error", "What happens", "What happens when you try to connect: an error message, or does it just "
                 "time out?", "text"),
        Question("timeout_kind", "Timeout", "Is the connection timing out, or do you need a timeout setting changed?",
                 "choice",
                 (("setting_change", ("setting", "change", "changed", "increase", "adjust", "configure", "longer",
                                      "extend")),
                  ("connection_timeout", ("timing out", "times out", "timed out", "time out", "timeout",
                                          "connection"))),
                 when=lambda v: bool(v.get("timeout_ambiguous"))),
        Question("location", "Location", "Are you in the office or working remotely?", "choice",
                 (("remote", ("home", "remote", "remotely", "away", "off site", "offsite", "travel")),
                  ("office", ("in the office", "office", "at work", "clinic", "on site", "onsite", "building")))),
        _SCOPE,
    ),
    next_action=lambda v: _server_access_next_action(v),
)


def _server_access_next_action(v: Values) -> str:
    """What IT should check, from what the caller established -- never a resource, cause or change nobody said."""
    steps = [f"Verify that {_servers(v)} is reachable and check its status from the caller's location"]
    kind = v.get("resource_type")
    if kind == "database":
        steps.append(f"check the database connection from {_said(v, 'db_client', 'the application that uses it')}")
    elif kind == "file_share":
        steps.append(f"check access to {_said(v, 'resource', 'the share or folder they need')}")
    elif kind == "application":
        steps.append(f"check {_said(v, 'application', 'the application they named')}")
    else:
        steps.append("establish which resource or service on it the caller needs")
    timeout = v.get("timeout_kind")
    if timeout == "setting_change":
        steps.append("the caller asked for a timeout setting to be changed: confirm which setting, and approval, "
                     "before changing anything")
    elif timeout == "connection_timeout":
        steps.append("connections to it are timing out")
    elif v.get("timeout_ambiguous"):
        steps.append("establish whether connections time out or a timeout setting needs changing")
    return "; ".join(steps) + ". Nothing was changed on the call."

NETWORK = Playbook(
    "network", "Network / VPN", noun="the network",
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
    "device", "Laptop / desktop", noun="your computer itself",
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

#: Words that say a login is failing without saying what for ("Yeah, I'm unable to log in.").
_LOGIN_WORDS = frozenset("""
    unable able can't cant cannot can not couldn't don't doesn't won't wont isn't to log login logon logging logged sign
    signing signin into get getting access accessing account accounts application applications app apps system
    password passwords credential credentials expired expire expiring working work works anymore anything any more
    let me locked out keeps keep saying says asking problem issue for one which what know think sure maybe not
    username user name trying try tried open
""".split())

ACCOUNT = Playbook(
    "account", "Account / access", noun="a login or account",
    questions=(
        Question("account", "Account or application",
                 "Which account or application can't you get into? Please don't tell me your password.", "text",
                 vague=_LOGIN_WORDS,
                 clarify="Which one is it, for example your computer sign-in, your email, or eClinicalWorks? "
                         "If you're not sure, just say so."),
        Question("error", "Error message", "What does the message say, exactly?", "text"),
        Question("worked_before", "Worked before", "Was it working for you before?", "yes_no"),
    ),
    next_action=lambda v: (
        f"Check the account status for {_said(v, 'account', 'the account')} (locked, expired, MFA) and the caller's "
        "access rights. The caller was not asked for, and should not be asked for, a password over the phone."
    ),
)

PRINTER = Playbook(
    "printer", "Printer / scanner", noun="a printer",
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

#: Desk devices other than printers. Seen on a test call: a wired keyboard got the printer questions ("Which printer
#: or device is it?", "a shared network printer?", "a paper jam?") and a print-queue next step.
_PERIPHERAL_DEVICES = (
    ("keyboard", ("keyboard", "keys", "key board")),
    ("mouse", ("mouse", "mice", "trackpad", "touchpad")),
    ("headset", ("headset", "headphone", "earphone", "earbud", "microphone")),
    ("monitor", ("monitor", "second screen", "external screen")),
    ("webcam", ("webcam", "web cam", "camera")),
    ("docking station", ("docking", "dock")),
    ("speakers", ("speaker",)),
)


def _peripheral_next_action(v: Values) -> str:
    device = v.get("device") if v.get("device") not in (None, "none") else "device"
    connection = {"wired": "the cable and the USB port", "wireless": "the batteries, the receiver or Bluetooth pairing"}
    check = connection.get(v.get("connection") or "", "how it is connected")
    state = {"dead": "completely unresponsive", "partial": "partly working (some keys or buttons)"}.get(
        v.get("symptom") or "", "symptom not established")
    return (f"Check the caller's {device} ({state}): {check}, then the device itself on another port or computer; "
            "replace it through the approved process if it is faulty.")


PERIPHERAL = Playbook(
    "peripheral", "Keyboard / mouse / other device", noun="a keyboard, mouse or other desk device",
    questions=(
        Question("device", "Device", "Which device is it: a keyboard, a mouse, a headset, a monitor, or something else?",
                 "choice", _PERIPHERAL_DEVICES),
        Question("connection", "Connection", "Is it connected with a cable, or is it wireless or Bluetooth?", "choice",
                 (("wireless", ("wireless", "bluetooth", "dongle", "receiver", "cordless")),
                  ("wired", ("wired", "cable", "usb", "cord", "plugged")))),
        Question("symptom", "Symptom",
                 "Is it completely unresponsive, or are only some keys or buttons not working?", "choice",
                 (("partial", ("some", "few", "certain", "a couple", "sticky", "stuck", "intermittent", "sometimes",
                               "on and off", "only")),
                  ("dead", ("completely", "unresponsive", "nothing", "not at all", "dead", "none of", "at all",
                            "whole", "entire")))),
    ),
    next_action=_peripheral_next_action,
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

PLAYBOOKS = {p.name: p for p in (OUTLOOK, SERVER_STORAGE, SERVER_ACCESS, NETWORK, DEVICE, ACCOUNT, PRINTER, PERIPHERAL,
                                 OTHER)}

#: Running out of space: only these words, never "server" alone. A server that "is not connecting" is an access
#: problem; storage is only asked about when the caller says something about space.
_STORAGE_WORDS = ("disk space", "out of space", "running out of space", "running low on space", "low on space",
                  "low on disk", "storage", "drive is full", "drive full", "disk is full", "disk full", "no space",
                  "space left", "free space", "server is full", "share is full", "volume is full", "full disk")
_SERVER_WORDS = ("server",)
_EMAIL_WORDS = ("email", "e-mail", "mail", "inbox", "calendar")
_PRINTER_WORDS = ("printer", "print", "scanner", "scan", "toner")
_PERIPHERAL_WORDS = tuple(w for _, words in _PERIPHERAL_DEVICES for w in words)


def _says(text: str, words: tuple[str, ...]) -> bool:
    """Any of the words, at the start of a word: "dock" is not "doctor", "print" is not "fingerprint"."""
    return any(re.search(rf"\b{re.escape(w)}", text) for w in words)


_NETWORK_WORDS = ("vpn", "wifi", "wi-fi", "internet", "network", "connection", "connect")
_ACCOUNT_WORDS = ("password", "locked out", "lock out", "log in", "login", "sign in", "signin", "access", "account",
                  "permission", "mfa", "authenticator")
_DEVICE_WORDS = ("laptop", "computer", "desktop", "pc", "screen", "turn on", "blue screen", "slow", "freez")


def choose(category: str | None, description: str | None) -> Playbook:
    """The question set for the problem as described. Server and Outlook words win over the broad categories."""
    text = (description or "").lower()
    if any(w in text for w in _STORAGE_WORDS):
        return SERVER_STORAGE
    if any(w in text for w in _SERVER_WORDS) or any(is_ip(n) for n in hostnames(description or "")):
        return SERVER_ACCESS
    if "outlook" in text or (category == "Microsoft 365" and any(w in text for w in _EMAIL_WORDS)):
        return OUTLOOK
    # The device the caller named decides, not the broad category: a keyboard the model filed under Printer still
    # gets keyboard questions, and only an actual printer or scanner gets printer ones.
    if any(w in text for w in _PRINTER_WORDS):
        return PRINTER
    if _says(text, _PERIPHERAL_WORDS):
        return PERIPHERAL
    if category == "Printer":
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
#: The caller does not know: "Don't know", "not sure", "no idea". Recorded as unknown -- the question is not asked
#: again, and the words never become a name, a drive or a system on the ticket ("access to Don't know").
_UNKNOWN = re.compile(
    r"^\W*(?:(?:um+|uh+|well|sorry|honestly)\W+)*(?:i\s+|i'?m\s+)?(?:(?:really\s+)?(?:don'?t|do\s+not|dont)\s+know|"
    r"not\s+sure|no\s+idea|no\s+clue|dunno|unknown|not\s+certain|have\s+no\s+idea|can'?t\s+remember)\b",
    re.IGNORECASE,
)
#: "Adjust the timeout": a setting to change, or a connection that times out? Asked, never assumed.
_TIMEOUT_AMBIGUOUS = re.compile(
    r"\b(?:adjust|change|increase|extend|raise|set|modify|update|fix|lengthen)\w*\s+(?:the\s+|a\s+|that\s+)?time\s?out\b"
    r"|\btime\s?out\s+(?:setting|value|limit|period)\b",
    re.IGNORECASE,
)
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


# A server name: letters then digits (FS01, hfmg-sql02, app01.hfmg.local) or letters-and-hyphens (HFMG-FILES).
_HOST = re.compile(r"(?<![\w.-])([A-Za-z][A-Za-z0-9]*(?:[-.][A-Za-z0-9]+)*)(?![\w-])")
_SPELLED_HOST = re.compile(r"(?<![\w-])(?:[A-Za-z0-9] ){2,}[A-Za-z0-9](?![\w-])")  # "F S 0 1"
# "FS 01", as a recognizer may write it. Only a space is joined: a hyphen is part of the name ("FS-BACKUP-02").
_SPLIT_HOST = re.compile(r"(?<![\w-])([A-Z]{2,6}) (\d{1,4})(?![\w-])")
_UNITS = re.compile(r"^\d*(?:k|m|g|t)?b$|^(?:gb|mb|tb|kb|gig|gigs)$", re.IGNORECASE)
_FILLER = {"um", "uh", "hmm", "mm", "er", "ah", "okay", "ok", "hello", "hi", "sorry", "what", "pardon", "yeah", "so"}
_LEAD_YES = ("yes", "yeah", "yep", "yup", "correct")
_LEAD_NO = ("no", "nope", "nah", "not really", "none")


# An address may end a sentence ("...is 172.22.6.203."): only a following digit or ".digit" makes it longer.
_IPV4 = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?!\d)(?!\.\d)")


def is_ip(value: str | None) -> bool:
    """A valid dotted IPv4 address ("172.22.6.98"); four parts, each 0-255."""
    match = _IPV4.fullmatch((value or "").strip())
    return bool(match) and all(0 <= int(part) <= 255 for part in match.group(1).split("."))


def hostnames(utterance: str) -> list[str]:
    """Server names and IP addresses in what the caller said, in the order said: "server ka naam FS01 hai" ->
    ["FS01"], "the server 172.22.6.98" -> ["172.22.6.98"].

    Conservative: a name must have a digit ("FS01") or a hyphen/dot inside ("HFMG-FILES"), so ordinary words
    in any language are never taken for one; an address must be a valid IPv4 address. Spelled-out characters
    ("F S 0 1") and a recognizer's split ("FS 01") are joined; nothing is otherwise changed or corrected."""
    text = utterance or ""
    found: list[tuple[int, str]] = []
    for match in _IPV4.finditer(text):
        if is_ip(match.group(1)):
            found.append((match.start(), match.group(1)))
    taken = [name for _, name in found]
    for match in _SPELLED_HOST.finditer(text):
        joined = match.group(0).replace(" ", "")
        if re.search(r"[A-Za-z]", joined) and re.search(r"\d", joined):
            found.append((match.start(), joined))
    for match in _SPLIT_HOST.finditer(text):
        found.append((match.start(), match.group(1) + match.group(2)))
    for match in _HOST.finditer(text):
        token = match.group(1).strip(".-")
        if len(token) < 3 or _UNITS.match(token) or any(token in ip for ip in taken):
            continue
        if not (re.search(r"\d", token) or re.search(r"[-.]", token)):
            continue
        if not any(token.lower() in f.lower() or f.lower() in token.lower() for _, f in found):
            found.append((match.start(), token))
    unique: list[str] = []
    for _, name in sorted(found, key=lambda item: item[0]):
        if name.lower() not in (u.lower() for u in unique):
            unique.append(name)
    return unique


def is_non_answer(utterance: str) -> bool:
    """Silence, "hello?", "um", "sorry, what?": nothing that answers a question."""
    words = re.findall(r"[a-z']+", (utterance or "").lower())
    has_digits = bool(re.search(r"\d", utterance or ""))
    return (not words and not has_digits) or nlu.is_greeting(utterance) or (bool(words) and not has_digits and all(w in _FILLER for w in words))


def _leading_yes_no(text: str) -> bool | None:
    lead = text.strip(" .,!")
    for words, answer in ((_LEAD_NO, False), (_LEAD_YES, True)):
        if any(lead == w or lead.startswith(w + " ") or lead.startswith(w + ",") for w in words):
            return answer
    return None


#: Words a short answer to a choice question may contain besides the choices themselves. Anything else in a
#: short answer ("Broken office.") means the recognizer may have misheard: the answer is unclear, not a choice.
_PLAIN_WORDS = {
    "i", "i'm", "im", "am", "a", "an", "the", "my", "in", "at", "on", "from", "of", "with", "and", "or", "it", "it's",
    "its", "is", "we", "we're", "our", "working", "work", "today", "now", "right", "currently", "just", "only",
    "yes", "yeah", "yep", "no", "um", "uh", "so", "well", "actually", "okay", "ok", "both", "there", "here", "still",
    "not", "aren't", "isn't", "don't", "doesn't", "won't", "can't", "keys", "key", "buttons", "button", "works",
}


def _short_answer_is_unclear(question: Question, utterance: str) -> bool:
    words = re.findall(r"[a-z']+", (utterance or "").lower())
    if not words or len(words) > 4:
        return False
    known = {w for _, keys in question.choices for key in keys for w in key.split()}
    return any(w not in _PLAIN_WORDS and not any(k in w or w in k for k in known) for w in words)


def _names_nothing(question: Question, utterance: str) -> bool:
    """For a question with `vague` words: the answer only restates the problem ("Yeah, I'm unable to log in.")
    and names no account, system or anything else."""
    if not question.vague:
        return False
    words = re.findall(r"[a-z0-9']+", (utterance or "").lower())
    return bool(words) and all(w in question.vague or w in _PLAIN_WORDS or w in _FILLER for w in words)


def _parse(question: Question, utterance: str) -> str | None:
    text = (utterance or "").lower()
    if question.kind != "yes_no" and _UNKNOWN.match(text) and len(text.split()) <= 8:
        return "unknown"
    if question.kind == "choice" and _short_answer_is_unclear(question, utterance):
        return None
    if question.kind == "yes_no":
        answer = nlu.quick_yes_no(utterance)
        if answer is None:  # a longer answer that starts with its yes or no ("No, the old apps still work, I just...")
            answer = _leading_yes_no(text)
        return None if answer is None else ("yes" if answer else "no")
    if question.kind == "choice":
        return next((value for value, words in question.choices if any(w in text for w in words)), None)
    if not text.strip() or is_non_answer(utterance):
        return None
    if _DECLINE.match(text) and len(text.split()) <= 5:
        return "none"
    if question.kind == "host":
        names = hostnames(utterance)
        return names[0] if names else clean(utterance)
    if _names_nothing(question, utterance):
        return None  # kept as what the caller said, never as the account or system they named
    return clean(utterance)


def begin(
    category: str | None, description: str | None, affected_scope: str | None, playbook: Playbook | None = None
) -> dict:
    """Pick the question set (or use `playbook`) and fill in what the description (or earlier answers) already say."""
    playbook = playbook or choose(category, description)
    values: Values = {}
    text = (description or "").lower()
    named = hostnames(description or "")
    for question in playbook.questions:
        if not question.when(values):
            # A question that does not apply is never answered from the description either. Seen on a test call:
            # "not connecting" filled in the timeout question, and the ticket claimed connections were timing out.
            continue
        found = _parse(question, description or "") if question.kind == "choice" else None
        if question.key == "server" and named:
            found = named[0]
            if len(named) > 1:
                values["additional_servers"] = ", ".join(named[1:])
        if question.kind == "text" and question.prefill_pattern:
            match = re.search(question.prefill_pattern, description or "", re.IGNORECASE)
            found = match.group(0).strip() if match else found
        if question.kind == "yes_no" and any(w in text for w in question.prefill) and not any(
            w in text for w in question.prefill_unless
        ):
            found = "yes"
        if found:
            values[question.key] = found
            values[f"{question.key}_said"] = "(from the problem description)"
    if affected_scope in ("one_person", "several_people", "whole_site") and any(q.key == "scope" for q in playbook.questions):
        values["scope"] = "no" if affected_scope == "one_person" else "yes"
        values["scope_said"] = "(from earlier in the call)"
    if playbook is SERVER_ACCESS and _TIMEOUT_AMBIGUOUS.search(description or ""):
        values["timeout_ambiguous"] = True
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
            return question.key, _prompt(question, values)
    return None


def _prompt(question: Question, values: Values) -> str:
    """The question as spoken, with what it names filled in ("I have the address 172.22.6.98"). Every ask and re-ask
    goes through here: a re-ask once said the raw "{server}" to a caller."""
    return question.prompt.format_map(_Known(values))


class _Known(dict):
    """For prompts that name an earlier answer ("I have the address {server}"): a missing value is blank."""

    def __missing__(self, key):
        return ""


CLARIFY = "Sorry, I didn't catch that."


# --- answers that are not answers: confusion, an echo of the question, a correction ------------------------------
#
# Seen on a test call: "Sorry, I didn't get you" was saved as the device and "Oh my God, you took the wrong..." as
# the error message, and "you understood it wrong" / "it's just the wired keyboard" were taken as answers to printer
# questions. Confusion is asked about again and never stored; a correction changes the questions to the right ones.

#: The caller did not follow the question.
_CONFUSED = re.compile(
    r"\b(?:did ?n'?t|did not|don'?t|do not|could ?n'?t|could not|can'?t)\s+(?:get|catch|understand|hear|follow)"
    r"(?:\s+(?:you|that|it|what you said|the question|what you mean))?\s*[.?!]*$"
    r"|\bwhat do you mean\b|\b(?:repeat|say) (?:that|it|the question)\b|\bsay again\b|\bcome again\b|\bpardon\b"
    r"|\bi'?m (?:confused|lost)\b|\bnot sure what you(?:'re| are)? asking\b|^\W*(?:sorry|what|huh|excuse me)\W*$",
    re.IGNORECASE,
)
#: The caller says the agent has the problem wrong.
_MISUNDERSTOOD = re.compile(
    r"\byou(?:'ve| have)?\s+(?:understood|understand|heard|got|took|taken|have)\s+(?:it\s+|me\s+|that\s+|this\s+)?"
    r"(?:all\s+)?wrong\b"
    r"|\b(?:took|got|picked|have|has)\s+the\s+wrong\b|\bmisunderst|\bmisheard\b"
    r"|\bthat'?s not what i (?:said|meant|mean)\b|\bwrong (?:device|thing|question|questions|problem|issue)\b"
    r"|\bnothing to do with\b|\bi (?:never|didn'?t|did not) (?:say|said|mention|mentioned)\b"
    r"|\bwhy\b[^.?!]{0,40}\b(?:asking|coming up|come up|came up|talking about|bringing up|ask me about)\b",
    re.IGNORECASE,
)
#: "it's not a printer", "not the printer": the device being turned down.
_NOT_THIS = re.compile(
    r"\b(?:it'?s|it is|this is|that'?s|that is|is)\s+not\s+(?:about\s+)?(?:a|an|the|my)\s+(?P<what>[a-z-]+)"
    r"|\bnot\s+(?:a|an|the)\s+(?P<other>[a-z-]+)",
    re.IGNORECASE,
)
#: "I said keyboard", "it's just the wired keyboard": the caller naming what it really is.
_I_SAID = re.compile(r"\b(?:i said|i told you|it'?s (?:just|only)|it is (?:just|only)|just (?:the|my|a))\b",
                     re.IGNORECASE)
_DEVICE_ADJECTIVE = r"(?:(?:wired|wireless|bluetooth|usb|external|new|old|second|work|office|desk|laptop)\s+)?"
#: A caller says the agent has it wrong this many times before the questions stop changing.
MAX_ISSUE_CORRECTIONS = 2

#: Mentioned devices and systems, in the order the question sets are chosen in (see `choose`).
_MENTION_ORDER = (
    (SERVER_STORAGE, _STORAGE_WORDS), (SERVER_ACCESS, _SERVER_WORDS), (OUTLOOK, ("outlook",) + _EMAIL_WORDS),
    (PRINTER, _PRINTER_WORDS), (PERIPHERAL, _PERIPHERAL_WORDS), (NETWORK, _NETWORK_WORDS),
    (ACCOUNT, _ACCOUNT_WORDS), (DEVICE, _DEVICE_WORDS),
)


def is_confused(question: Question | None, utterance: str) -> bool:
    """"Sorry, I didn't get you", "What do you mean?", or the question read back to the agent."""
    if _CONFUSED.search(utterance or ""):
        return True
    return question is not None and _echoes(question.prompt, utterance)


_QUESTION_WORDS = frozenset({"which", "what", "where", "who", "how", "is", "are", "do", "does", "did", "can", "could"})


def _echoes(prompt: str, utterance: str) -> bool:
    """The caller repeating the question ("Which printer or device is it?") rather than answering it."""
    said = re.findall(r"[a-z']+", (utterance or "").lower())
    asked = set(re.findall(r"[a-z']+", (prompt or "").lower()))
    # Said as a question: an answer that picks one of the options offered ("A shared drive.", "The connection is
    # timing out.") repeats the question's words because it answers it, and must never be taken for an echo.
    as_question = (utterance or "").strip().endswith("?") or bool(said) and said[0] in _QUESTION_WORDS
    return as_question and len(said) >= 3 and sum(w in asked for w in said) / len(said) >= 0.8


def _mentioned(text: str) -> list[Playbook]:
    lowered = (text or "").lower()
    return [playbook for playbook, words in _MENTION_ORDER if _says(lowered, words)]


def _device_phrase(text: str, playbook: Playbook) -> str | None:
    """How the caller named the device ("the wired keyboard" -> "wired keyboard")."""
    words = dict(_MENTION_ORDER).get(playbook) or ()
    lowered = (text or "").lower()
    for word in sorted(words, key=len, reverse=True):
        match = re.search(rf"\b{_DEVICE_ADJECTIVE}{re.escape(word.strip())}\w*", lowered)
        if match:
            return match.group(0).strip()
    return None


def correction(state: dict, utterance: str) -> dict | None:
    """The caller says the questions are about the wrong thing. None when they did not.

    Returns {"playbook": the question set for what they say it really is, or None when they did not say, "thing":
    how they named it}. Only an explicit correction counts -- "it says something went wrong" is an error message,
    not a correction -- and after MAX_ISSUE_CORRECTIONS the questions stop changing."""
    if (state.get("corrections") or 0) >= MAX_ISSUE_CORRECTIONS:
        return None
    text = utterance or ""
    current = PLAYBOOKS[state["playbook"]]
    others = [p for p in _mentioned(text) if p is not current]
    turned_down = [m.group("what") or m.group("other") for m in _NOT_THIS.finditer(text)]
    rejects_current = any(current in _mentioned(word) for word in turned_down if word)
    if not (_MISUNDERSTOOD.search(text) or rejects_current or (_I_SAID.search(text) and others)):
        return None
    new = others[0] if others else None
    return {"playbook": new.name if new else None, "thing": _device_phrase(text, new) if new else None}


def restart(state: dict, playbook: Playbook, description: str | None, said: str, affected_scope: str | None) -> dict:
    """Start the right question set after a correction. What was recorded under the wrong one is dropped (it
    answered the wrong questions); the correction itself is kept for the ticket."""
    fresh = begin(None, f"{description or ''} {said}", affected_scope, playbook=playbook)
    from_description = begin(None, description, affected_scope, playbook=playbook)["values"]
    for key in [k for k in fresh["values"] if k.endswith("_said")]:
        if fresh["values"][key] == "(from the problem description)" and key not in from_description:
            fresh["values"][key] = "(from the caller's correction)"  # say where it really came from
    fresh["values"]["issue_corrected"] = clean(said)
    fresh["values"]["issue_corrected_from"] = PLAYBOOKS[state["playbook"]].label
    fresh["corrections"] = (state.get("corrections") or 0) + 1
    fresh["flags"] = sorted(set(fresh["flags"]) | set(state.get("flags") or []))
    return fresh


def asking_what_it_is(state: dict) -> dict:
    """The caller said it is wrong but not what it is: ask, and keep the corrections count."""
    return {**state, "pending": None, "asking_issue": True, "corrections": (state.get("corrections") or 0) + 1,
            "issue_corrected_said": None}


def named_in_answer(state: dict, utterance: str) -> tuple[Playbook, str | None]:
    """The answer to "what is giving you trouble?": the question set for what they named, else Other."""
    current = PLAYBOOKS[state["playbook"]]
    others = [p for p in _mentioned(utterance) if p is not current]
    new = others[0] if others else OTHER
    return new, (_device_phrase(utterance, new) if new is not OTHER else None)


def restatement(old: Playbook, new: Playbook, thing: str | None) -> str:
    """"Sorry about that. I understand now that you're having a problem with your wired keyboard, not a printer.\""""
    if not thing:
        return "Sorry about that."
    not_that = f", not {old.noun}" if old.noun and old is not new else ""
    return f"Sorry about that. I understand now that you're having a problem with your {thing}{not_that}."


def record(state: dict, utterance: str) -> tuple[dict, dict, str | None]:
    """Apply the caller's answer to the pending question.

    Returns (new state, updates for `collected`, a re-ask or None). A non-answer (silence, "hello?", "um") is
    asked about once more, briefly; a second one moves on with that question unanswered. Confusion ("Sorry, I
    didn't get you") and the question read back are treated the same way: asked again, never stored as the answer."""
    state = {**state, "values": dict(state["values"]), "asked": list(state["asked"]),
             "retried": list(state.get("retried") or [])}
    key = state.get("pending")
    _flag(state, utterance)
    updates: dict = {}
    question = next((q for q in PLAYBOOKS[state["playbook"]].questions if q.key == key), None)
    if question is not None and state.get("conflict"):
        return _resolve_conflict(state, utterance, question)
    if question is not None and question.kind == "host" and not is_non_answer(utterance):
        conflict = _server_conflict(state["values"], hostnames(utterance))
        if conflict:
            state["conflict"] = {"key": question.key, "new": conflict, "said": clean(utterance)}
            old = state["values"]["server"]
            return state, updates, f"Is {conflict} another server that's affected, a correction to {old}, or another address for the same server?"
    unclear_choice = question is not None and question.kind == "choice" and not is_non_answer(utterance) and (
        _parse(question, utterance) is None
    )
    confused = question is not None and not is_non_answer(utterance) and is_confused(question, utterance)
    if question is not None and (is_non_answer(utterance) or unclear_choice or confused) and question.key not in state["retried"]:
        state["retried"].append(question.key)
        return state, updates, f"{CLARIFY} {_prompt(question, state['values'])}"
    vague = question is not None and not _DECLINE.match((utterance or "").lower()) and _names_nothing(question, utterance)
    if vague and question.key not in state["retried"]:
        # Heard, but it names nothing ("Yeah, I'm unable to log in."): not "I didn't catch that" -- which one.
        state["retried"].append(question.key)
        return state, updates, question.clarify or _prompt(question, state["values"])
    if question is not None:
        state["asked"].append(question.key)
        value = None if confused else _parse(question, utterance)
        if value is not None:
            state["values"][question.key] = value
        if utterance.strip() and not is_non_answer(utterance) and not confused:
            state["values"][f"{question.key}_said"] = clean(utterance)
        if question.kind == "host":
            extra = hostnames(utterance)[1:]
            if extra:  # "FS01 and FS02": both are affected
                state["values"]["additional_servers"] = ", ".join(extra)
        if state["playbook"] == "server_access" and _TIMEOUT_AMBIGUOUS.search(utterance or ""):
            state["values"]["timeout_ambiguous"] = True
        if question.key == "scope" and value == "yes":
            updates["affected_scope"] = "whole_site" if "everyone" in state["flags"] else "several_people"
        elif question.key == "scope" and value == "no":
            updates["affected_scope"] = "one_person"
    state["pending"] = None
    return state, updates, None


# --- a server mentioned late: another one, or a correction --------------------------------------------------------

_ANOTHER_SERVER = re.compile(
    r"\b(?:another|second|other|one more|additional|a different|also (?:a|the|one))\s+(?:\w+\s+)?server\b"
    r"|\bserver\b[^.?!]{0,30}\b(?:too|as well|also)\b",
    re.IGNORECASE,
)
_CORRECTION = ("wrong", "correct", "replace", "instead", "mistake", "not that", "rather", "change it", "meant")
_KEEP_BOTH = ("another", "also", "as well", "both", "in addition", "additional", "second", "too", "more than one")


def mentions_another_server(utterance: str) -> bool:
    return bool(_ANOTHER_SERVER.search(utterance or ""))


def server_change_kind(utterance: str) -> str:
    """"correction" only when the caller says so; anything else (including an unclear answer) is "additional",
    so an earlier server name is never overwritten on a guess."""
    text = (utterance or "").lower()
    if any(w in text for w in _CORRECTION) and not any(w in text for w in _KEEP_BOTH):
        return "correction"
    return "additional"


def add_server(state: dict, name: str | None, said: str, kind: str) -> dict:
    """Record a server mentioned after the server question: an additional affected server, or a correction
    (the earlier name is kept as "corrected from", never lost)."""
    values = dict(state["values"])
    said = clean(said)
    name = name or said or "name not given"
    if kind == "correction" and values.get("server"):
        values["server_corrected_from"] = values.get("server_said") or values["server"]
        values["server"], values["server_said"] = name, said or name
    elif not values.get("server"):
        values["server"], values["server_said"] = name, said or name
    else:
        existing = [n for n in (values.get("additional_servers") or "").split(", ") if n]
        if name.lower() not in [n.lower() for n in existing] + [str(values.get("server", "")).lower()]:
            existing.append(name)
        values["additional_servers"] = ", ".join(existing)
        before = values.get("additional_servers_said")
        values["additional_servers_said"] = f"{before} | {said}" if before else said
    return {**state, "values": values}


def _server_conflict(values: Values, names: list[str]) -> str | None:
    """A different address (or name) for the server than the one already known, when it cannot be told which
    it is: another affected server, a correction, or another identifier for the same server. A hostname given
    for a known IP address (or the other way round) is not a conflict: it is the server's name."""
    current = values.get("server")
    if not current or not names:
        return None
    new = names[0]
    if new.lower() == str(current).lower():
        return None
    if is_ip(current) != is_ip(new):
        return None  # "FS01" for 172.22.6.98: the name of the server already known
    return new


_SAME_SERVER = ("same server", "the same", "same one", "same machine", "same box", "another address", "other address",
                "second address", "its address", "its ip", "its other", "also its", "alias", "both addresses")


def server_identifier_kind(utterance: str) -> str:
    """How a second address relates to the first: "same" (another identifier for the same server),
    "correction", "additional" -- or "unclear", which keeps both and says so."""
    text = (utterance or "").lower()
    if any(w in text for w in ("not sure", "don't know", "dont know", "no idea", "not certain", "unsure")):
        return "unclear"  # "I'm not sure, I just know both numbers": both kept, nothing decided for the caller
    if any(w in text for w in _SAME_SERVER):
        return "same"
    if any(w in text for w in _CORRECTION) and not any(w in text for w in _KEEP_BOTH):
        return "correction"
    if any(w in text for w in _KEEP_BOTH) or nlu.quick_yes_no(utterance) is True:
        return "additional"
    return "unclear"


def _resolve_conflict(state: dict, utterance: str, question: Question) -> tuple[dict, dict, str | None]:
    conflict = state.pop("conflict")
    kind = server_identifier_kind(utterance)
    said = conflict["said"] + (f" / {clean(utterance)}" if utterance.strip() else "")
    values = state["values"]
    if kind in ("additional", "correction"):
        state = add_server(state, conflict["new"], said, kind)
        values = state["values"]
    elif kind == "same":
        aliases = [a for a in (values.get("server_aliases") or "").split(", ") if a]
        values["server_aliases"] = ", ".join([*aliases, conflict["new"]])
    else:  # unclear: both kept, and the ticket says the relationship was not established
        open_ = [a for a in (values.get("server_unclarified") or "").split(", ") if a]
        values["server_unclarified"] = ", ".join([*open_, conflict["new"]])
    values[f"{question.key}_resolved"] = True
    state["values"] = values
    state["asked"] = [*state["asked"], question.key]
    state["pending"] = None
    return state, {}, None


def ticket_lines(
    state: dict | None, description: str | None = None, category: str | None = None
) -> list[tuple[str, str]]:
    """The diagnostics section of the ticket's intake details: each answer in the caller's words, what is still
    missing, the escalation reason, and a suggested next step for IT.

    With `description`: a ticket whose questions no longer match the final description (it was changed at the
    read-back, say) is flagged for review rather than filed looking consistent."""
    if not state:
        return []
    playbook = PLAYBOOKS[state["playbook"]]
    values = state.get("values") or {}
    lines: list[tuple[str, str]] = [("Issue type", playbook.label)]
    if values.get("issue_corrected"):
        lines.append(("Issue corrected", f"the caller said the first questions ({values.get('issue_corrected_from')}) "
                                         f"were wrong: \"{values['issue_corrected']}\""))
    elif description:
        expected = choose(category, description)
        if expected is not playbook:
            lines.append(("Issue type check", f"the questions asked were for {playbook.label}, but the description "
                                              f"reads as {expected.label}; review before acting"))
    missing = []
    for question in playbook.questions:
        said = values.get(f"{question.key}_said")
        value = values.get(question.key)
        if values.get(f"{question.key}_resolved") and value is None:
            continue  # answered by the server lines below (another address, a correction, ...)
        if value is None and said is None:
            if question.when(values):
                missing.append(question.label.lower())
            continue
        shown = said if said and not said.startswith("(") else value
        if said and said.startswith("("):
            shown = f"{value} {said}"
        if question.kind == "yes_no" and value in ("yes", "no") and said and not said.startswith("("):
            shown = f"{value} (caller: \"{said}\")"
        if question.kind == "host" and value and said and value != said and not said.startswith("("):
            shown = f"{value} (caller said: \"{said}\")"
        if question.kind == "choice" and said and not said.startswith("("):
            shown = f"{value} (caller said: \"{said}\")" if value else f"unclear (caller said: \"{said}\")"
        if value == "unknown":
            shown = f"unknown (caller said: \"{said}\")" if said and not said.startswith("(") else "unknown"
        if question.kind == "text" and value is None and said and not said.startswith("("):
            shown = f"not identified (caller said: \"{said}\")"
        lines.append((question.label, shown or "unclear"))
        if question.key == "server" and values.get("server_corrected_from"):
            lines.append(("Server (corrected)", f"the caller corrected it from \"{values['server_corrected_from']}\""))
        if question.key == "server" and values.get("additional_servers"):
            extra = values["additional_servers"]
            if values.get("additional_servers_said"):
                extra = f"{extra} (caller said: \"{values['additional_servers_said']}\")"
            lines.append(("Additional servers", extra))
        if question.key == "server" and values.get("server_aliases"):
            lines.append(("Also known as", f"{values['server_aliases']} (the caller said it is the same server)"))
        if question.key == "server" and values.get("server_unclarified"):
            lines.append(("Other address given", f"{values['server_unclarified']} (not established whether it is "
                                                  f"another server, a correction, or the same server)"))
    reason = urgent_reason(state)
    if reason:
        lines.append(("Escalation", f"flagged urgent: {reason}"))
    if missing:
        lines.append(("Not established", ", ".join(missing)))
    lines.append(("Suggested next step", playbook.next_action(values)))
    return lines
