"""English / Spanish detection for picking the reply voice.

Whisper is left on auto-detect, and the agent answers in the language it was
spoken to in. Each reply sentence is classified here so Kokoro reads it with
the matching voice and pronunciation. Stdlib only and deliberately small: it
only has to tell two languages apart, sentence by sentence.

Ambiguous sentences ("OK.", "Docker 27.3.") keep the language of the previous
one, so a reply doesn't switch voice in the middle.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Literal

Language = Literal["en", "es"]

# Characters that only appear in Spanish (of the two).
_SPANISH_CHARS = frozenset("ñ¿¡áéíóúü")

# Frequent words that belong to one language only. Words both share ("no",
# "me", "a", "son", "come"...) are left out on purpose.
_SPANISH_WORDS = frozenset(
    """
    el la los las del que y en un una es por para con se lo su al como mas pero
    sus le ya este porque esta entre cuando muy sin sobre tambien hasta hay donde
    quien desde todo nos durante todos uno les ni contra otros ese eso ante ellos
    esto antes algunos unos yo otro otras otra tanto esa estos mucho nada muchos
    cual poco ella estar estas algo nosotros mi mis tu te ti tus ellas os hola
    gracias bueno vale esta estan estoy hace tiene tengo puedo puedes puede
    quieres quiero necesito dime oye cuanto cuantos cual cuales que como donde
    hoy manana ayer ahora tiempo dia noche buenos buenas tarde tardes
    favor claro verdad pues entonces aqui alli asi
    """.split()
)
_ENGLISH_WORDS = frozenset(
    """
    i the and is are of to it that you for on with this what how be was were have
    has do does did can could would should will my your we they there at from
    about an or if but not just all so get got like what's it's i'm don't can't
    i'll you're that's here which when where who why please thanks thank hello
    hi yes yeah okay sure good morning evening tonight today tomorrow yesterday
    weather time much many some any one two three know think want need tell
    """.split()
)
_WORD = re.compile(r"[a-z']+")


def fold(text: str) -> str:
    """Lowercase and drop accents, one character for one ("Cállate" -> "callate").

    Length-preserving, so positions found in the folded text are valid in the
    original.
    """
    out: list[str] = []
    for char in text.lower():
        decomposed = unicodedata.normalize("NFKD", char)
        out.append(decomposed[0] if decomposed else char)
    return "".join(out)


def detect_language(text: str, default: Language = "en") -> Language:
    """Best guess between English and Spanish; ``default`` when unclear.

    >>> detect_language("¿Qué hora es?")
    'es'
    >>> detect_language("What time is it?")
    'en'
    >>> detect_language("OK.", default="es")
    'es'
    """
    lower = text.lower()
    spanish = 2 * sum(1 for char in lower if char in _SPANISH_CHARS)
    english = 0
    for word in _WORD.findall(fold(text)):
        if word in _SPANISH_WORDS:
            spanish += 1
        elif word in _ENGLISH_WORDS:
            english += 1
    if spanish > english:
        return "es"
    if english > spanish:
        return "en"
    return default
