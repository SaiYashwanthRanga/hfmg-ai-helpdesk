"""How sure are we of a caller's name?

Speech recognition gets ordinary English names right and gets unfamiliar ones
wrong ("Yashwanth" -> "Yashwandh", "Reilly" -> "Riley", "Natakarani" ->
"Natekereni"), and nothing in the audio says which is which. So confidence is
built from independent signals, and only doubtful names cost the caller a
confirmation turn:

- the caller spelled it                       -> confident (letters are decoded exactly)
- every part is a very common name           -> confident, unless another signal disagrees
- the language model flagged it as uncertain -> doubtful
- recognizer confidence for the utterance is low (when the provider reports it) -> doubtful
- anything else (an unfamiliar name)          -> doubtful

The common-name lists are deliberately short and Anglo/Hispanic-heavy: they mean
"the recognizer reliably gets this right", not "this is a real name". A name
that is real but not on the list simply gets one confirmation.
"""

import functools
import re
import unicodedata

COMMON_FIRST = frozenset(
    """
    james john robert michael william david richard joseph thomas tom charles chris christopher daniel matthew
    anthony mark donald steven paul andrew joshua kenneth kevin brian george timothy ronald jason edward jeffrey
    ryan jacob gary nicholas eric jonathan stephen larry justin scott brandon benjamin samuel gregory alexander
    frank patrick raymond jack dennis jerry tyler aaron jose adam nathan henry douglas zachary peter kyle walter
    ethan jeremy harold keith christian roger noah gerald carl terry sean austin arthur lawrence jesse dylan
    bryan joe jordan billy bruce albert willie gabriel logan alan juan wayne roy ralph randy eugene vincent
    russell elijah louis bobby philip johnny bradley luis carlos miguel antonio manuel jorge victor omar
    mary patricia jennifer linda elizabeth barbara susan jessica sarah karen lisa nancy betty margaret sandra
    ashley kimberly emily donna michelle carol amanda dorothy melissa deborah stephanie rebecca sharon laura
    cynthia kathleen amy angela shirley anna brenda pamela emma nicole helen samantha katherine christine
    debra rachel carolyn janet catherine maria heather diane ruth julie olivia joyce virginia victoria kelly
    lauren christina joan evelyn judith megan andrea cheryl hannah jacqueline martha gloria teresa ann sara
    madison frances kathryn janice jean abigail alice julia judy sophia grace denise amber doris marilyn
    danielle beverly isabella theresa diana natalie brittany charlotte marie kayla alexis lori rosa carmen
    ana lucia sofia isabel elena gabriela patricia yolanda veronica monica
    """.split()
)

COMMON_LAST = frozenset(
    """
    smith johnson williams brown jones garcia miller davis rodriguez martinez hernandez lopez gonzalez wilson
    anderson thomas taylor moore jackson martin lee perez thompson white harris sanchez clark ramirez lewis
    robinson walker young allen king wright scott torres nguyen hill flores green adams nelson baker hall
    rivera campbell mitchell carter roberts gomez phillips evans turner diaz parker cruz edwards collins reyes
    stewart morris morales murphy cook rogers gutierrez ortiz morgan cooper peterson bailey reed kelly howard
    ramos kim cox ward richardson watson brooks chavez wood james bennett gray mendoza ruiz hughes price alvarez
    castillo sanders myers long ross foster jimenez powell jenkins perry russell sullivan bell coleman
    butler henderson barnes gonzales fisher vasquez simmons romero jordan patterson alexander hamilton graham
    reynolds griffin wallace moreno west cole hayes bryant herrera gibson ellis tran medina aguilar stevens
    murray ford castro marshall owens harrison fernandez mcdonald woods washington kennedy wells vargas henry
    chen freeman webb tucker guzman burns crawford olson simpson porter hunter gordon mendez silva shaw snyder
    mason dixon munoz hunt hicks holmes palmer wagner black robertson boyd rose stone salazar fox warren mills
    meyer rice schmidt garza daniels ferguson nichols stephens soto weaver ryan gardner payne grant dunn kelley
    spencer hawkins arnold pierce vazquez hansen peters santos hart bradley knight elliott cunningham duncan
    armstrong hudson carroll lane riley andrews alvarado ray delgado berry perkins hoffman johnston matthews
    pena richards contreras willis carpenter lawrence sandoval guerrero george chapman rios estrada ortega
    watkins greene nunez wheeler valdez harper burke larson santiago maldonado morrison franklin carlson
    """.split()
)

#: Below this recognizer confidence (0-1) a name is confirmed even if it is common.
LOW_STT_CONFIDENCE = 0.85


def parts(name: str | None) -> list[str]:
    return [p for p in re.split(r"[\s\-]+", (name or "").lower()) if re.sub(r"[^a-z]", "", p)]


def letters(word: str | None) -> str:
    """A name part reduced to its lowercase letters (any alphabet): "O'Brien" and "OBrien" are one name."""
    return re.sub(r"[\W\d_]+", "", (word or "").casefold())


#: Spellings that sound alike, rewritten to one form before names are compared by sound. A recognizer writes an
#: unfamiliar name as it sounds: "Yashvant" for Yashwanth, "Riley" for Reilly, "Natakarani" for Natekereni.
_SOUNDS_ALIKE = (("ph", "f"), ("th", "t"), ("dh", "d"), ("bh", "b"), ("kh", "k"), ("gh", "g"), ("ck", "k"),
                 ("w", "v"), ("z", "s"), ("q", "k"), ("x", "ks"))


@functools.lru_cache(maxsize=8192)
def sound_key(word: str | None) -> str:
    """A name part as it sounds: accents dropped, sound-alike spellings made one, a silent h after a consonant
    dropped, doubled letters made single, and every run of vowels after the first letter made one "a". Two spellings
    of one spoken name get the same key; the first letter is kept as written."""
    plain = "".join(c for c in unicodedata.normalize("NFKD", letters(word)) if not unicodedata.combining(c))
    for spelled, sound in _SOUNDS_ALIKE:
        plain = plain.replace(spelled, sound)
    plain = re.sub(r"c(?=[eiy])", "s", plain).replace("c", "k")
    plain = re.sub(r"(?<=[^aeiouy])h", "", plain)
    plain = re.sub(r"(.)\1+", r"\1", plain)
    return plain[:1] + re.sub(r"[aeiouy]+", "a", plain[1:])


#: How alike two name parts are (name_similarity): the same letters, one letter off, the same sound, or only close.
EXACT, ONE_LETTER, SAME_SOUND = 1.0, 0.97, 0.95
#: Below this a name part is not taken for another. Fuzzy scores are at most 0.9, so only a near sound passes.
SIMILAR_ENOUGH = 0.75


def name_similarity(heard: str | None, listed: str | None) -> float:
    """How alike a name part the caller said is to one on file, 0 to 1. Short names (under four letters) must be
    exact: "Li" and "Lee" are different people. Otherwise one letter off (not the first: "Dana" is not "Lana"), the
    same sound (sound_key: "Catherine" is "Katherine"), or a close sound (edit distance between the sound keys, scaled,
    and never above 0.9) count; the first sound must agree."""
    a, b = letters(heard), letters(listed)
    if not a or not b:
        return 0.0
    if a == b:
        return EXACT
    if min(len(a), len(b)) < 4:
        return 0.0
    if a[0] == b[0] and edit_distance(a, b) <= 1:
        return ONE_LETTER
    ka, kb = sound_key(a), sound_key(b)
    if ka == kb:
        return SAME_SOUND
    if ka[:1] != kb[:1]:
        return 0.0
    return round(0.9 * (1 - edit_distance(ka, kb) / max(len(ka), len(kb))), 3)


def edit_distance(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def same_name_part(heard: str, listed: str) -> bool:
    """One part of a name as the caller said it, against the same part as written: equal, or one letter off in a
    name of four letters or more (a recognizer's slip: "Reilly" for "Riley"). Shorter names must be exact."""
    return heard == listed or (len(heard) >= 4 and edit_distance(heard, listed) <= 1)


#: Words a recognizer or a model hands back as a "name" that are not one. Seen on a test call: "Hi, name" was taken
#: as the name and read back as "N A M E".
_NOT_A_NAME = frozenset({
    "name", "names", "my", "is", "hi", "hello", "hey", "yes", "yeah", "no", "nope", "okay", "ok", "sorry", "change",
    "correct", "wrong", "spell", "spelling", "spelled", "it", "that", "this", "the", "please", "thanks", "thank",
    "you", "what", "sir", "madam", "me", "i", "um", "uh", "right", "good", "fine", "here", "there", "speaking",
    "calling", "caller", "unknown", "agent", "not", "and", "but", "so",
})


def plausible(name: str | None) -> bool:
    """Could this be a person's name? False for "Name", "Hi name", "my name", "yes" and the like. Deliberately
    narrow: it only rules out words that are plainly not names, never an unusual or unfamiliar one."""
    words = [re.sub(r"[^a-z]", "", p) for p in parts(name)]
    words = [w for w in words if w]
    return bool(words) and not any(w in ("name", "names") for w in words) and not all(w in _NOT_A_NAME for w in words)


def _known(part: str) -> bool:
    word = re.sub(r"[^a-z]", "", part)
    return word in COMMON_FIRST or word in COMMON_LAST


#: Below this recognizer confidence the name is probably wrong, not just unconfirmed: ask for the
#: spelling straight away instead of reading back a likely mishearing and waiting for a "no".
VERY_LOW_STT_CONFIDENCE = 0.6


def needs_spelling(
    name: str | None, *, spelled: bool = False, model_confidence: str | None = None, stt_confidence: float | None = None
) -> bool:
    """True when the agent should ask for the name's spelling right away. Only on positive evidence
    of a poor hearing (the recognizer's own score, or the model flagging it as doubtful while the
    recognizer was also not sure): never because a name is merely unusual."""
    if spelled or not parts(name) or stt_confidence is None:
        return False
    if stt_confidence < VERY_LOW_STT_CONFIDENCE:
        return True
    return model_confidence == "low" and stt_confidence < LOW_STT_CONFIDENCE


def name_confidence(
    name: str | None,
    *,
    spelled: bool = False,
    model_confidence: str | None = None,
    stt_confidence: float | None = None,
) -> str:
    """"high" or "low". Low means: read it back before trusting it."""
    if not parts(name):
        return "low"
    if spelled:
        return "high"
    if model_confidence == "low":
        return "low"
    if stt_confidence is not None and stt_confidence < LOW_STT_CONFIDENCE:
        return "low"
    if not all(_known(p) for p in parts(name)):
        return "low"
    return "high"
