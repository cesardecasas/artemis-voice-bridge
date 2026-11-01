"""Sentence splitting for streamed TTS (artemis/speech.py)."""

from __future__ import annotations

import pytest

from artemis.speech import SentenceSplitter, clean_for_speech


def split(*tokens: str) -> list[str]:
    splitter = SentenceSplitter()
    sentences: list[str] = []
    for token in tokens:
        sentences += splitter.feed(token)
    return sentences + splitter.flush()


def test_sentences_are_released_as_soon_as_complete() -> None:
    splitter = SentenceSplitter()
    assert splitter.feed("Hello there") == []
    assert splitter.feed(".") == []  # could still be "there.com" or "3.5"
    assert splitter.feed(" How") == ["Hello there."]
    assert splitter.flush() == ["How"]


def test_works_token_by_token() -> None:
    assert split(*"Sure. I can do that!\nNext line") == ["Sure.", "I can do that!", "Next line"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Dr. Smith is here. Yes.", ["Dr. Smith is here.", "Yes."]),
        ("Version 3.5 is out. Great", ["Version 3.5 is out.", "Great"]),
        ("J. R. R. Tolkien wrote it. Indeed.", ["J. R. R. Tolkien wrote it.", "Indeed."]),
        ('He said "stop." Then left.', ['He said "stop."', "Then left."]),
        ("Use e.g. this one. Ok", ["Use e.g. this one.", "Ok"]),
        ("Wait... what?! Really", ["Wait...", "what?!", "Really"]),
    ],
)
def test_tricky_boundaries(text: str, expected: list[str]) -> None:
    assert split(text) == expected


def test_code_blocks_are_not_spoken_or_split() -> None:
    tokens = ["Run this: ```bash\necho hi. done\n", "``` After that, nothing.", " End"]
    assert split(*tokens) == ["Run this: After that, nothing.", "End"]


def test_long_runs_without_punctuation_are_split() -> None:
    sentences = split("word, " * 100)
    assert len(sentences) > 1
    assert all(len(sentence) <= 280 for sentence in sentences)


def test_markdown_is_cleaned() -> None:
    assert clean_for_speech("**Bold** and `code` and [a link](http://x) # head") == (
        "Bold and code and a link head"
    )
    assert clean_for_speech("- item one\n- item two") == "item one item two"


def test_empty_pieces_are_skipped() -> None:
    assert split("```\ncode only\n```") == []
    assert split("   ") == []
