"""Deterministic phrase gate: does the caller's own wording actually state the fact?

The model proposes a fact; this module decides, with plain patterns, whether the
words support it. Nothing here asks a model, so it cannot be talked into
anything: a fact is accepted only if a *strong* pattern matches what the caller
said, and a contradicting pattern ("... but I can still work") makes it a
conflict, not a guess. Anything the patterns do not recognise is simply not
recognised; the caller is asked instead.

Patterns are deliberately narrow. A missed phrase costs one clarification
question; a wrong match costs a wrong priority. They run on normalized text
(lower case, punctuation removed, apostrophes kept: see facts.normalize_text).
"""

import re
from dataclasses import dataclass

from app.voice.facts import PATIENT_CARE, SCOPE, WORK_BLOCKED, normalize_text


@dataclass(frozen=True)
class Verdict:
    """What the words say about one field."""

    value: bool | str | None = None   # the stated value, or None if nothing explicit
    quote: str | None = None          # the words that state it (as matched)
    conflict: bool = False            # both a true and a false statement (or two scopes) were found
    rule: str | None = None           # name of the pattern family that matched


# --- building blocks ---------------------------------------------------------------------

# "can't", "cannot", "am not able to", "unable to" ...
_NEG = (
    r"(?:can't|cannot|can not|cant|couldn't|could not|won't be able to|"
    r"(?:am|are|is|was|were)\s+(?:not|no longer)\s+able to|(?:am|are|is)\s+unable to|unable to|not able to)"
)
_SUBJECT = r"(?:i|we|they|he|she|you|everyone|everybody|users|staff|nurses|providers|doctors|team|nobody|noone)"
_ADV = r"(?:(?:really|just|also|still|currently|all|now|even|actually)\s+)*"
_WORK_OBJECT = (
    r"(?:work\b(?!\s+(?:with|on|from|in|out|it|this|that|around|through)\b)"
    r"|function\b"
    r"|do\s+(?:my|our|their|his|her|any)\s+(?:of\s+(?:my|our|their)\s+)?(?:job|jobs|work|duties|tasks)\b"
    r"|do\s+anything\b"
    r"|do\s+a\s+thing\b"
    r"|get\s+(?:any|my|our|their|anything)(?:\s+work)?\s+done\b)"
)
_PATIENT_VERB = r"(?:check(?:ing)?[- ]?in|see(?:ing)?|treat(?:ing)?|schedul(?:e|ing)|regist(?:er|ering)|admit(?:ting)?)"
_PATIENT_NOUN = r"(?:patients?|anyone|anybody|people)"


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern)


# --- work_blocked ------------------------------------------------------------------------

_BLOCKED_TRUE = {
    # "I can't work", "we are not able to do any work", "I can't do my job", "I can't do anything"
    "cannot_work": _rx(rf"\b{_SUBJECT}\s+{_ADV}{_NEG}\s+{_ADV}{_WORK_OBJECT}"),
    # "nobody up front can do anything"
    "nobody_can_work": _rx(
        r"\b(?:nobody|no one|none of us|none of the \w+)\s+(?:\w+\s+){0,4}(?:can|is able to|are able to)\s+"
        r"(?:do anything|work\b|do (?:any|their|our) work|do (?:their|our) jobs?)"
    ),
    # "stopping me from working", "preventing us from doing our jobs"
    "stopping_me": _rx(
        r"\b(?:stopping|preventing|blocking|keeping)\s+(?:me|us|him|her|them|my team|the team|everyone)\s+from\s+"
        r"(?:working|doing\s+(?:my|our|any|their)\s+\w+|getting\s+(?:any|my|our)?\s*work\s+done)"
    ),
    # "I'm completely blocked / stuck", "we are stuck"
    "i_am_blocked": _rx(r"\b(?:i'm|i am|im|we're|we are)\s+(?:all\s+|completely\s+|totally\s+|fully\s+)?(?:blocked|stuck)\b"),
    # "the whole front desk is stuck"
    "group_blocked": _rx(
        r"\b(?:front desk|team|department|staff|office|everyone|everybody|nurses|providers|billing)\s+"
        r"(?:is|are)\s+(?:all\s+|completely\s+|totally\s+)?(?:stuck|blocked)\b"
    ),
    # "I can't check in any patients", "we can't check anyone in", "I can't see patients"
    "cannot_serve_patients": _rx(
        rf"\b{_SUBJECT}\s+{_ADV}{_NEG}\s+(?:\w+\s+){{0,2}}{_PATIENT_VERB}\s+(?:(?:any|our|the|my|more|new)\s+)*{_PATIENT_NOUN}\b"
    ),
    "cannot_check_anyone_in": _rx(rf"\b{_NEG}\s+(?:\w+\s+){{0,1}}check(?:ing)?\s+{_PATIENT_NOUN}\s+in\b"),
    # Login failure only counts when it is the computer itself, or "at all":
    # "can't log in to Outlook" is one application and is NOT enough on its own.
    "cannot_log_in_to_device": _rx(
        rf"\b{_NEG}\s+log(?:ging)?\s*in\s+(?:to|on|into)\s+(?:my|the|our)\s+(?:own\s+)?"
        r"(?:computer|pc|laptop|workstation|windows|desktop|machine|network|domain)\b"
    ),
    "cannot_log_in_at_all": _rx(rf"\b{_NEG}\s+log(?:ging)?\s*in\s+at all\b"),
    "locked_out_of_device": _rx(
        r"\b(?:locked out of|can't get into|cannot get into|can't access|cannot access)\s+(?:my|the|our)\s+(?:own\s+)?"
        r"(?:computer|pc|laptop|workstation|windows|desktop|machine)\b"
    ),
    "past_login_screen": _rx(rf"\b{_NEG}\s+get\s+past\s+(?:the\s+)?(?:log\s?in|sign\s?in)\s+(?:screen|page)\b"),
}

_BLOCKED_FALSE = {
    # "I can still work", "we can still use it", "I can still type it in by hand"
    "can_still": _rx(r"\b(?:i|we|they)\s+(?:can|could)\s+still\s+(?:work|do|get|use|access|see|type|manage|function|continue|print|read)\b"),
    "can_work": _rx(r"\b(?:i|we)\s+can\s+work\b(?!\s+(?:from|with|on|out)\b)"),
    "keep_working": _rx(r"\b(?:can|able to|am able to|are able to)\s+(?:still\s+)?(?:keep|continue|carry on)\s+working\b"),
    "still_working": _rx(r"\b(?:i'm|i am|we're|we are)\s+still\s+(?:able to\s+)?work(?:ing)?\b"),
    "workaround": _rx(r"\bwork\s?around\b"),
    "using_instead": _rx(
        r"\b(?:i'm|i am|we're|we are|i can)\s+(?:just\s+)?(?:using|use|going with)\s+(?:my|a|the|another|an)\s+"
        r"(?:phone|cell|laptop|other|different|backup|spare|paper|webmail|personal)\b"
    ),
    "on_my_phone": _rx(
        r"\b(?:can|could)\s+(?:still\s+)?(?:get|check|read|see|use|access)\s+(?:my\s+)?\w+\s+(?:on|from|through|via)\s+"
        r"my\s+(?:phone|cell|mobile|laptop|iphone|ipad)\b"
    ),
    "not_stopping_me": _rx(r"\b(?:not|isn't|isnt|doesn't|doesnt|is not|does not)\s+(?:really\s+)?(?:stopping|preventing|blocking|keeping)\s+(?:me|us)\b"),
    "can_use_alternative": _rx(r"\bi can use\s+(?:the\s+|my\s+)?(?:paper|phone|other|another|backup)\b"),
}

# --- patient_care_affected ----------------------------------------------------------------

_PATIENT_TRUE = {
    "cannot_check_in": _rx(
        rf"\b{_SUBJECT}\s+{_ADV}{_NEG}\s+(?:\w+\s+){{0,2}}{_PATIENT_VERB}\s+(?:(?:any|our|the|my|more|new)\s+)*{_PATIENT_NOUN}\b"
    ),
    "cannot_check_anyone_in": _rx(rf"\b{_NEG}\s+(?:\w+\s+){{0,1}}check(?:ing)?\s+{_PATIENT_NOUN}\s+in\b"),
    "patients_cannot": _rx(
        r"\bpatients?\s+(?:can't|cannot|can not|aren't able to|are not able to|are unable to|couldn't)\s+(?:be\s+)?"
        r"(?:checked[- ]in|seen|treated|registered|scheduled|check in)\b"
    ),
    "cannot_open_patient_records": _rx(
        rf"\b{_NEG}\s+(?:\w+\s+){{0,2}}(?:open|pull up|access|view|see|get to|get into|look up|reach)\s+(?:\w+\s+){{0,3}}"
        r"patients?\s+(?:charts?|records?|files?|information|orders?|results?|labs?|medications?)\b"
    ),
    "cannot_open_charts_for_patients": _rx(
        rf"\b{_NEG}\s+(?:\w+\s+){{0,2}}(?:open|pull up|access|view|see|get to|look up)\s+(?:the\s+)?"
        r"(?:charts?|orders?|lab results?|test results?)\s+(?:for|of)\s+(?:\w+\s+){0,2}patients?\b"
    ),
    "nobody_can_open_patient_charts": _rx(
        r"\b(?:none of|nobody|no one)\b(?:\s+\w+){0,5}\s+(?:can|is able to|are able to)\s+(?:\w+\s+){0,2}"
        r"(?:open|pull up|access|view|see|check in|treat|reach)\s+(?:\w+\s+){0,3}(?:patients?|patient charts?|charts?|records?)\b"
    ),
}

_PATIENT_FALSE = {
    "care_not_affected": _rx(r"\bpatient care\s+(?:is\s+)?(?:not|isn't|is not)\s+(?:being\s+)?(?:affected|impacted|blocked|held up|delayed)\b"),
    "not_affecting_care": _rx(r"\b(?:not|isn't|no)\s+(?:affecting|impacting|impact on)\s+patient care\b"),
    "patients_not_affected": _rx(r"\bpatients?\s+(?:are|is)\s+(?:not|aren't|isn't)\s+(?:being\s+)?(?:affected|impacted)\b"),
}

# --- affected_scope ------------------------------------------------------------------------

_SCOPE = {
    "whole_site": {
        "whole_or_entire_site": _rx(
            r"\b(?:whole|entire)\s+(?:[a-z]+\s+)?(?:office|site|clinic|building|practice|location|network|company|organization)\b"
        ),
        "everyone_at_site": _rx(
            r"\b(?:everyone|everybody|nobody|no one|all of us)\s+(?:in|at|on)\s+(?:the\s+|our\s+)?(?:office|site|clinic|building|practice|location)\b"
        ),
        "all_sites": _rx(r"\ball\s+(?:of\s+)?(?:our\s+)?(?:sites|offices|locations|clinics)\b"),
    },
    "several_people": {
        "my_team": _rx(r"\b(?:my|our)\s+(?:whole\s+|entire\s+)?(?:team|department|unit|floor)\b"),
        "whole_group": _rx(
            r"\b(?:whole|entire)\s+(?:front desk|billing(?:\s+department)?|department|team|floor|unit|lab|nursing staff|staff)\b"
        ),
        "some_of_us": _rx(r"\b(?:several|a few|some|all|two|three|four|many|most)\s+of us\b"),
        "nobody_in_group": _rx(r"\b(?:nobody|no one)\s+in\s+(?:the\s+)?(?!office\b|site\b|clinic\b|building\b)\w+\b"),
        "none_of_the_staff": _rx(r"\bnone of the (?:providers|nurses|doctors|staff|people|users)\b"),
        "nobody_up_front": _rx(r"\b(?:nobody|no one)\s+(?:up front|at the front desk|at the desk|in the back|on the floor)\b"),
    },
    "one_person": {
        "just_me": _rx(r"\b(?:just|only)\s+(?:me|my\s+(?:computer|laptop|pc|machine|desktop|workstation|account|phone))\b"),
        "nobody_else": _rx(r"\b(?:nobody|no one)\s+else\b|\bonly\s+(?:happening\s+)?(?:to|for|on)\s+me\b"),
        "rest_fine": _rx(r"\b(?:the\s+)?rest of\s+(?:the\s+)?team\s+(?:is|are)\s+(?:fine|ok|okay|working)\b"),
    },
}


def _first_hit(patterns: dict[str, re.Pattern], text: str) -> tuple[str, str] | None:
    for name, rx in patterns.items():
        match = rx.search(text)
        if match:
            return name, match.group(0).strip()
    return None


def inspect(field: str, text: str) -> Verdict:
    """What `text` explicitly says about `field`. Pure and deterministic.

    Booleans: a strong "true" pattern, a strong "false" pattern, neither, or both
    (a conflict). Scope: exactly one scope's patterns, or a conflict if several match.
    """
    normalized = normalize_text(text)
    if not normalized:
        return Verdict()

    if field in (WORK_BLOCKED, PATIENT_CARE):
        true_patterns, false_patterns = (
            (_BLOCKED_TRUE, _BLOCKED_FALSE) if field == WORK_BLOCKED else (_PATIENT_TRUE, _PATIENT_FALSE)
        )
        hit_true = _first_hit(true_patterns, normalized)
        hit_false = _first_hit(false_patterns, normalized)
        if hit_true and hit_false:
            return Verdict(conflict=True, rule=f"{hit_true[0]}+{hit_false[0]}")
        if hit_true:
            return Verdict(True, hit_true[1], rule=hit_true[0])
        if hit_false:
            return Verdict(False, hit_false[1], rule=hit_false[0])
        return Verdict()

    if field == SCOPE:
        hits = {value: _first_hit(patterns, normalized) for value, patterns in _SCOPE.items()}
        found = {value: hit for value, hit in hits.items() if hit}
        if len(found) > 1:
            return Verdict(conflict=True, rule="+".join(sorted(found)))
        if found:
            (value, (rule, quote)), = found.items()
            return Verdict(value, quote, rule=rule)
        return Verdict()

    raise ValueError(f"no phrase gate for field {field!r}")
