"""Turn streamed agent text into speakable sentences (Phase 5).

The agent streams tokens; TTS wants whole sentences (prosody is much better,
and a sentence is the smallest unit that sounds natural). ``SentenceSplitter``
buffers tokens and releases each sentence as soon as it is complete, so the
first sentence can be spoken while the rest of the reply is still generating.
"""

from __future__ import annotations

import re

# End of sentence: terminal punctuation (plus closing quotes/brackets) followed
# by whitespace, or a line break. Punctuation at the very end of the buffer is
# not a boundary yet: the next token might be "5" (as in "3.5") or "com".
_BOUNDARY = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s)|\n")

# Words ending in "." that don't end a sentence.
_ABBREVIATIONS = frozenset(
    {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e",
     "approx", "no", "fig", "inc", "ltd", "co", "mt", "a.m", "p.m", "u.s"}
)

# Fallback for very long runs without punctuation: split at a comma or space.
MAX_SENTENCE_CHARS = 280
_SOFT_BREAK = re.compile(r"[,;:]\s|\s")

_FENCED_CODE = re.compile(r"```.*?(```|\Z)", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MARKUP = re.compile(r"[*_#>~|]+")
_SPACES = re.compile(r"\s+")


def clean_for_speech(text: str) -> str:
    """Strip markdown that would be read aloud literally ("asterisk asterisk")."""
    text = _FENCED_CODE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = _MARKUP.sub(" ", text)
    text = re.sub(r"^\s*[-+]\s+", "", text, flags=re.MULTILINE)
    return _SPACES.sub(" ", text).strip()


def _is_abbreviation(buffer: str, dot_index: int) -> bool:
    """True if the "." at ``dot_index`` ends an abbreviation or an initial."""
    if buffer[dot_index] != ".":
        return False
    word = re.search(r"([\w.]+)$", buffer[:dot_index])
    if not word:
        return False
    token = word.group(1).lower()
    return token in _ABBREVIATIONS or (len(token) == 1 and token.isalpha())


class SentenceSplitter:
    """Incremental sentence splitter.

    >>> splitter = SentenceSplitter()
    >>> splitter.feed("Hello there. How")
    ['Hello there.']
    >>> splitter.feed(" are you?")
    []
    >>> splitter.flush()
    ['How are you?']
    """

    def __init__(self, max_chars: int = MAX_SENTENCE_CHARS) -> None:
        self._buffer = ""
        self.max_chars = max_chars

    def feed(self, text: str) -> list[str]:
        """Add streamed text; return any sentences that are now complete."""
        self._buffer += text
        sentences: list[str] = []
        while (sentence := self._take()) is not None:
            if sentence:
                sentences.append(sentence)
        return sentences

    def flush(self) -> list[str]:
        """Return whatever is left (end of the reply)."""
        rest, self._buffer = self._buffer, ""
        spoken = clean_for_speech(rest)
        return [spoken] if spoken else []

    def _take(self) -> str | None:
        """Remove one complete sentence from the buffer. None if there isn't one."""
        buffer = self._buffer

        # Fenced code is never spoken, so never split inside it. An unclosed
        # fence runs to the end of the buffer until its closing ``` arrives.
        fences = [match.span() for match in _FENCED_CODE.finditer(buffer)]

        for match in _BOUNDARY.finditer(buffer):
            if any(start <= match.start() < end for start, end in fences):
                continue
            if _is_abbreviation(buffer, match.start()):
                continue
            return self._cut(match.end())

        if buffer.count("```") % 2 == 1:
            return None

        if len(buffer) > self.max_chars:
            breaks = list(_SOFT_BREAK.finditer(buffer, 0, self.max_chars))
            return self._cut(breaks[-1].end() if breaks else self.max_chars)
        return None

    def _cut(self, end: int) -> str:
        sentence, self._buffer = self._buffer[:end], self._buffer[end:]
        return clean_for_speech(sentence)
