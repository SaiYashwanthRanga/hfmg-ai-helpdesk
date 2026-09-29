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

import re

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


def _known(part: str) -> bool:
    word = re.sub(r"[^a-z]", "", part)
    return word in COMMON_FIRST or word in COMMON_LAST


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
