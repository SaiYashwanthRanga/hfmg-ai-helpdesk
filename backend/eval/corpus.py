"""Voice evaluation corpus: realistic callers with ground truth.

Each scenario is one phone call. `answers` holds what the caller says in
reply to each agent state; the harness plays whichever answer matches the
question the agent is actually asking, so a scenario stays valid however the
conversation branches. Utterances are written the way people talk on the
phone -- fillers, self-corrections, run-ons, digits as words -- not the way
they type.

`truth` is what a correct intake should end up with. `started`,
`department` and `issue` are matched loosely (any listed keyword); a list
for `priority` means any of those levels is defensible under nlu.py's
guidance; everything else must match exactly.
"""

SCENARIOS = [
    {
        "id": "simple_outlook",
        "style": "plain",
        "voice": "coral",
        "instructions": "A calm office worker calling IT on the phone. Natural, conversational pace.",
        "answers": {
            "COLLECT_DESCRIPTION": "Hi, yeah, my Outlook won't open. It just keeps spinning.",
            "COLLECT_DETAILS": "It started this morning when I logged in. I can still get my email on my phone, so I'm working, just slower.",
            "COLLECT_NAME": "It's Maria Lopez, I'm in billing.",
            "COLLECT_PHONE": "Eight four five, five five five, zero one four two.",
            "COLLECT_EMAIL": "It's m lopez at h f m g dot net.",
            "CONFIRM_EMAIL": "Yes, please.",
            "CONFIRM_CATEGORY": "Yes.",
            "ANYTHING_ELSE": "No, that's all, thank you.",
        },
        "truth": {
            "caller_name": "Maria Lopez", "department": ["billing"], "phone": "8455550142",
            "email": "mlopez@hfmg.net", "category": "Microsoft 365", "priority": "MEDIUM",
            "started": ["morning"], "work_blocked": False, "issue": ["outlook"],
        },
    },
    {
        "id": "volunteers_everything",
        "style": "long",
        "voice": "ash",
        "instructions": "A rushed front-desk worker at a busy medical office. Talks in one long run-on sentence with 'um' and 'like'.",
        "answers": {
            "COLLECT_DESCRIPTION": (
                "Hey, good morning, this is James Carter from the front desk at the Newburgh office, um, so since "
                "about eight thirty today none of us can log into eClinicalWorks, like the whole front desk, "
                "we literally can't check patients in at all."
            ),
            "COLLECT_DETAILS": "Since about eight thirty, and no, nobody up front can do anything.",
            "COLLECT_NAME": "James Carter, front desk.",
            "COLLECT_PHONE": "It's eight four five, five five five, zero one seven seven.",
            "COLLECT_EMAIL": "j carter at h f m g dot net.",
            "CONFIRM_EMAIL": "Yeah, that's right.",
            "CONFIRM_CATEGORY": "Yes.",
            "ANYTHING_ELSE": "Nope, that's it, please hurry.",
        },
        "truth": {
            "caller_name": "James Carter", "department": ["front desk"], "phone": "8455550177",
            "email": "jcarter@hfmg.net", "category": "eClinicalWorks", "priority": "URGENT",
            "started": ["8:30", "8.30", "eight thirty", "830"], "work_blocked": True, "issue": ["eclinicalworks", "ecw", "log"],
        },
    },
    {
        "id": "hesitant_password",
        "style": "slow",
        "voice": "sage",
        "instructions": "An older, hesitant caller who is not confident with computers. Speaks slowly, with long pauses, 'uh' and 'um', and restarts sentences.",
        "answers": {
            "COLLECT_DESCRIPTION": "Uh... yeah, so, um, my... my computer, it keeps... it says my password expired? And I, uh, I can't get in at all.",
            "COLLECT_DETAILS": "Um, just now, this morning. And no, I can't do anything, I can't get past the login screen.",
            "COLLECT_NAME": "Oh, uh, Priya... Priya Shah. I work in radiology.",
            "COLLECT_PHONE": "Um, eight four five... five five five... zero two three one.",
            "COLLECT_EMAIL": "Uh, p shah, at, h f m g, dot net.",
            "CONFIRM_EMAIL": "Yes, that's it.",
            "CONFIRM_CATEGORY": "Yes, I think so.",
            "ANYTHING_ELSE": "No, no, that's everything. Thank you, dear.",
        },
        "truth": {
            "caller_name": "Priya Shah", "department": ["radiology"], "phone": "8455550231",
            "email": "pshah@hfmg.net", "category": "Password", "priority": "HIGH",
            "started": ["morning", "now", "today"], "work_blocked": True, "issue": ["password"],
        },
    },
    {
        "id": "fast_printer",
        "style": "fast",
        "voice": "ballad",
        "instructions": "A very fast talker who is busy and clipped. Speaks quickly with no pauses.",
        "answers": {
            "COLLECT_DESCRIPTION": "Front desk printer's jammed again, we're using the one in the back for now.",
            "COLLECT_DETAILS": "Since like ten minutes ago, and yeah we can still work, we're using the back one.",
            "COLLECT_NAME": "Tom Reilly, reception.",
            "COLLECT_PHONE": "Eight four five five five five zero three three three.",
            "COLLECT_EMAIL": "Skip.",
            "CONFIRM_EMAIL": "Yep.",
            "CONFIRM_CATEGORY": "Yep.",
            "ANYTHING_ELSE": "Nope, thanks.",
        },
        "truth": {
            "caller_name": "Tom Reilly", "department": ["reception"], "phone": "8455550333",
            "email": None, "category": "Printer", "priority": "MEDIUM",
            "started": ["ten minutes", "10 minutes"], "work_blocked": False, "issue": ["printer", "jam"],
        },
    },
    {
        "id": "accent_indian_vpn",
        "style": "accent",
        "voice": "echo",
        "instructions": "A caller with a strong Indian English accent, polite and formal.",
        "answers": {
            "COLLECT_DESCRIPTION": "Good afternoon. My VPN is not connecting from home since yesterday evening, I am not able to do any work.",
            "COLLECT_DETAILS": "From yesterday evening only. No, I am completely stuck, I cannot do anything.",
            "COLLECT_NAME": "My name is Suresh Kumar, from the finance department.",
            "COLLECT_PHONE": "Nine one four, five five five, zero four four four.",
            "COLLECT_EMAIL": "Suresh dot kumar at h f m g dot net.",
            "CONFIRM_EMAIL": "Yes, correct.",
            "CONFIRM_CATEGORY": "Yes.",
            "ANYTHING_ELSE": "No, that is all. Thank you so much.",
        },
        "truth": {
            "caller_name": "Suresh Kumar", "department": ["finance"], "phone": "9145550444",
            "email": "suresh.kumar@hfmg.net", "category": "Network", "priority": "HIGH",
            "started": ["yesterday"], "work_blocked": True, "issue": ["vpn"],
        },
    },
    {
        "id": "accent_spanish_scanner",
        "style": "accent",
        "voice": "nova",
        "instructions": "A caller with a noticeable Spanish accent, friendly, medium pace.",
        "answers": {
            "COLLECT_DESCRIPTION": "Hello, the scanner in the lab is making a grinding noise, but it still scans.",
            "COLLECT_DETAILS": "Since last week, maybe Thursday. And yes, I can still work, it still scans.",
            "COLLECT_NAME": "Lucia Ortiz, from the laboratory.",
            "COLLECT_PHONE": "Eight four five, five five five, zero five five one.",
            "COLLECT_EMAIL": "l ortiz at h f m g dot net.",
            "CONFIRM_EMAIL": "Yes, is correct.",
            "CONFIRM_CATEGORY": "Yes.",
            "ANYTHING_ELSE": "No, thank you.",
        },
        "truth": {
            "caller_name": "Lucia Ortiz", "department": ["lab"], "phone": "8455550551",
            "email": "lortiz@hfmg.net", "category": "Printer", "priority": "LOW",
            "started": ["last week", "thursday"], "work_blocked": False, "issue": ["scanner"],
        },
    },
    {
        "id": "self_correction_teams",
        "style": "plain",
        "voice": "verse",
        "instructions": "A slightly flustered caller who corrects themselves mid-sentence.",
        "answers": {
            "COLLECT_DESCRIPTION": "So my Teams is— actually no, sorry, it's Outlook, my Outlook calendar won't sync, it hasn't since Monday.",
            "COLLECT_DETAILS": "Since Monday. I can still work, I'm just missing meeting invites.",
            "COLLECT_NAME": "Daniel Nguyen, I'm in HR.",
            "COLLECT_PHONE": "Eight four five, five five five, zero six six two.",
            "COLLECT_EMAIL": "d nguyen, that's n g u y e n, at h f m g dot net.",
            "CONFIRM_EMAIL": "That's right.",
            "CONFIRM_CATEGORY": "Yes.",
            "ANYTHING_ELSE": "No, that's everything.",
        },
        "truth": {
            "caller_name": "Daniel Nguyen", "department": ["hr", "human resources"], "phone": "8455550662",
            "email": "dnguyen@hfmg.net", "category": "Microsoft 365", "priority": "MEDIUM",
            "started": ["monday"], "work_blocked": False, "issue": ["outlook", "calendar"],
        },
    },
    {
        "id": "vague_slow_pc",
        "style": "plain",
        "voice": "onyx",
        "instructions": "A tired caller giving a vague description, low energy.",
        "answers": {
            "COLLECT_DESCRIPTION": "Yeah, something's wrong with my computer, it's just really, really slow.",
            "COLLECT_DETAILS": "A couple of days now. I can work, it's just painful.",
            "COLLECT_NAME": "Omar Haddad, accounts payable.",
            "COLLECT_PHONE": "Eight four five, five five five, zero seven one seven.",
            "COLLECT_EMAIL": "o haddad at h f m g dot net.",
            "CONFIRM_EMAIL": "Yes.",
            "CONFIRM_CATEGORY": "Sure, yes.",
            "ANYTHING_ELSE": "No.",
        },
        "truth": {
            "caller_name": "Omar Haddad", "department": ["accounts payable", "accounts"], "phone": "8455550717",
            "email": "ohaddad@hfmg.net", "category": "Other", "priority": ["MEDIUM", "LOW"],
            "started": ["couple of days", "two days", "2 days"], "work_blocked": False, "issue": ["slow"],
        },
    },
    {
        # Modelled on a real tester: an uncommon Indian name and an email
        # built from it -- exactly what transcription gets wrong.
        "id": "indian_name_spelling",
        "style": "accent",
        "voice": "ash",
        "instructions": "A young IT-savvy caller with an Indian English accent, speaking at a normal, slightly quick pace.",
        "answers": {
            "COLLECT_DESCRIPTION": "Hi, my Outlook password was reset and now I can't log in to my email at all.",
            "COLLECT_DETAILS": "It started about an hour ago, and no, I can't do my work without email.",
            "COLLECT_NAME": "My name is Yashwanth Ranga, I'm in the IT department.",
            "COLLECT_PHONE": "Two one four, eight eight five, nine zero eight nine.",
            "COLLECT_EMAIL": "It's ranga dot saiyashwanth at h f m g dot net.",
            "CONFIRM_EMAIL": "Yes, that's correct.",
            "CONFIRM_CATEGORY": "Yes.",
            "ANYTHING_ELSE": "No, thank you.",
        },
        "truth": {
            "caller_name": "Yashwanth Ranga", "department": ["it"], "phone": "2148859089",
            "email": "ranga.saiyashwanth@hfmg.net", "category": "Password", "priority": ["HIGH"],
            "started": ["hour"], "work_blocked": True, "issue": ["password", "outlook"],
        },
    },
    {
        "id": "wants_human",
        "style": "plain",
        "voice": "shimmer",
        "instructions": "An impatient caller who does not want to talk to a machine.",
        "answers": {
            "COLLECT_DESCRIPTION": "Can I just talk to a real person, please?",
            # Asked only when there's no caller ID, so the callback has a number.
            "COLLECT_PHONE": "Eight four five, five five five, zero eight eight eight.",
        },
        "truth": {"escalated": True},
    },
    {
        "id": "digits_and_skip",
        "style": "plain",
        "voice": "fable",
        "instructions": "A matter-of-fact caller, clear diction.",
        "answers": {
            "COLLECT_DESCRIPTION": "I got locked out of eClinicalWorks after too many tries, and I've got patients waiting.",
            "COLLECT_DETAILS": "About twenty minutes ago. No, I can't see my schedule or chart anything.",
            "COLLECT_NAME": "Grace Kim, family medicine.",
            "COLLECT_PHONE": "It's eight four five, five five five, zero one nine nine.",
            "COLLECT_EMAIL": "I'd rather not, skip that.",
            "CONFIRM_EMAIL": "Yes.",
            "CONFIRM_CATEGORY": "Yes.",
            "ANYTHING_ELSE": "No, thanks.",
        },
        "truth": {
            "caller_name": "Grace Kim", "department": ["family medicine"], "phone": "8455550199",
            "email": None, "category": "Password", "priority": ["HIGH", "URGENT"],
            "started": ["twenty minutes", "20 minutes"], "work_blocked": True, "issue": ["locked", "eclinicalworks"],
        },
    },
]

# Realistic recovery (`e2e --realistic`): a caller who hears their email read
# back wrong says no, then spells it when asked to.
SPELLED_EMAIL = {
    "simple_outlook": "m, l, o, p, e, z, at h f m g dot net.",
    "volunteers_everything": "j, c, a, r, t, e, r, at h f m g dot net.",
    "hesitant_password": "p, s, h, a, h, at h f m g dot net.",
    "accent_indian_vpn": "s, u, r, e, s, h, dot, k, u, m, a, r, at h f m g dot net.",
    "accent_spanish_scanner": "l, o, r, t, i, z, at h f m g dot net.",
    "self_correction_teams": "d, n, g, u, y, e, n, at h f m g dot net.",
    "vague_slow_pc": "o, h, a, d, d, a, d, at h f m g dot net.",
    "indian_name_spelling": "r, a, n, g, a, dot, s, a, i, y, a, s, h, w, a, n, t, h.",
}
for _scenario in SCENARIOS:
    if _scenario["id"] in SPELLED_EMAIL:
        _scenario["answers"]["COLLECT_EMAIL_SPELLED"] = SPELLED_EMAIL[_scenario["id"]]
        _scenario["answers"]["CONFIRM_EMAIL_NO"] = "No, that's not right."

# The agent reads the name back spelled. A caller whose name was misheard
# says no, then spells first and last name when asked.
for _scenario in SCENARIOS:
    _name = _scenario["truth"].get("caller_name")
    if not _name:
        continue
    _first, _, _last = _name.partition(" ")
    _scenario["answers"]["CONFIRM_NAME"] = "Yes, that's right."
    # The agent reads the whole ticket back before creating it.
    _scenario["answers"]["CONFIRM_SUMMARY"] = "Yes, that's right."
    _scenario["answers"]["CONFIRM_NAME_NO"] = "No, that's not right."
    _scenario["answers"]["SPELL_FIRST"] = ", ".join(_first.lower()) + "."
    if _last:
        _scenario["answers"]["SPELL_LAST"] = ", ".join(_last.lower()) + "."

# Scenarios replayed with background noise added (~10 dB SNR).
NOISY = ["simple_outlook", "volunteers_everything", "hesitant_password", "accent_indian_vpn", "indian_name_spelling"]
