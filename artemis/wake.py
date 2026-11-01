"""Wake word and spoken approvals, matched on Whisper transcripts (Phase 7).

There is no pretrained "Artemis" wake-word model, and training one takes tens
of GB of data. Instead, hands-free mode transcribes each speech segment with
the local Whisper service (~130 ms on the B50) and checks whether it *starts*
with the wake word. Whisper already knows the name (``initial_prompt``), and a
single sentence carries both: "Artemis, what's the weather?".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from artemis.language import fold

WAKE_WORD = "artemis"

# How Whisper tends to spell it when it mishears.
_KNOWN_VARIANTS = frozenset(
    {"artemis", "artemus", "artimis", "artamis", "ardemis", "artemiss", "artemas", "artemies"}
)
_LEADING_FILLER = frozenset(
    {"hey", "hi", "ok", "okay", "hello", "yo", "oh", "um", "uh", "oye", "hola", "eh", "ey", "bueno"}
)
_WORD = re.compile(r"[a-z0-9']+")
_SIMILARITY = 0.8


def _words(text: str) -> list[str]:
    """Lowercase words without accents ("¿Qué?" -> ["que"])."""
    return _WORD.findall(fold(text))


def _is_wake_word(word: str, wake_word: str) -> bool:
    if word.endswith("'s"):
        return False  # "Artemis's settings": talking *about* Artemis
    if word == wake_word or (wake_word == WAKE_WORD and word in _KNOWN_VARIANTS):
        return True
    return SequenceMatcher(None, word, wake_word).ratio() >= _SIMILARITY


@dataclass(frozen=True, slots=True)
class WakeMatch:
    command: str
    """What followed the wake word ("" if the wake word was said alone)."""


def match_wake(text: str, wake_word: str = WAKE_WORD) -> WakeMatch | None:
    """``WakeMatch`` if ``text`` starts with the wake word (after an optional "hey").

    >>> match_wake("Artemis, what time is it?")
    WakeMatch(command='what time is it?')
    >>> match_wake("Hey Artemus.")
    WakeMatch(command='')
    >>> match_wake("I told Artemis about it") is None
    True
    """
    wake_word = wake_word.lower()
    words = _words(text)
    index = 0
    while index < len(words) - 1 and index < 2 and words[index] in _LEADING_FILLER:
        index += 1
    if index >= len(words) or not _is_wake_word(words[index], wake_word):
        return None

    # Cut the original text after the wake word, keeping the user's casing and punctuation.
    # fold() keeps one character per character, so positions carry over.
    folded = fold(text)
    position = 0
    for word in words[: index + 1]:
        position = folded.index(word, position) + len(word)
    command = text[position:].lstrip(" ,.!?;:-—").strip()
    return WakeMatch(command)


# Matched on accent-free words, so "sí" is "si". Spanish after the English.
_APPROVE = re.compile(
    r"^(yes|yeah|yep|sure|ok|okay|approve[ds]?|approved|do it|go ahead|go for it|confirm(ed)?|proceed|run it"
    r"|si|claro|vale|dale|adelante|siga|sigue|hazlo|apruebo|aprobado|confirmo|confirmado|de acuerdo|procede)\b"
)
_DENY = re.compile(
    r"^(no|nope|nah|deny|denied|don'?t|do not|cancel|stop|abort|reject(ed)?|never ?mind"
    r"|niego|denegado|deniega|rechazo|rechazado|rechaza|cancela|cancelalo|para|detente|mejor no|olvidalo)\b"
)


def match_approval(text: str) -> bool | None:
    """True for "approve"/"yes"..., False for "deny"/"no"..., None if unclear.

    >>> match_approval("Yes, go ahead.")
    True
    >>> match_approval("No, don't do that")
    False
    >>> match_approval("What does it do?") is None
    True
    """
    spoken = " ".join(_words(text))
    wake = match_wake(text)
    if wake is not None:
        spoken = " ".join(_words(wake.command))
    if _DENY.match(spoken):
        return False
    if _APPROVE.match(spoken):
        return True
    return None
