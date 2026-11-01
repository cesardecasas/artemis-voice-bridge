"""English / Spanish detection used to pick the reply voice."""

from __future__ import annotations

import pytest

from artemis.language import detect_language, fold


@pytest.mark.parametrize(
    "text",
    ["¿Qué hora es?", "Son las ocho de la noche.", "Hola Artemis, ¿cómo estás?",
     "El servidor está funcionando sin problemas.", "Gracias.", "Apaga las luces de la cocina por favor"],
)
def test_spanish(text: str) -> None:
    assert detect_language(text) == "es"


@pytest.mark.parametrize(
    "text",
    ["What time is it?", "The server is running fine.", "Thanks.", "Turn off the kitchen lights please",
     "It's eight in the evening."],
)
def test_english(text: str) -> None:
    assert detect_language(text, default="es") == "en"


@pytest.mark.parametrize("text", ["OK.", "Docker 27.3.", "Artemis.", "", "No."])
def test_unclear_keeps_the_default(text: str) -> None:
    assert detect_language(text, default="es") == "es"
    assert detect_language(text, default="en") == "en"


def test_fold_keeps_length() -> None:
    text = "¿Cállate, Señor Íñigo!"
    assert fold(text) == "¿callate, senor inigo!"
    assert len(fold(text)) == len(text)
