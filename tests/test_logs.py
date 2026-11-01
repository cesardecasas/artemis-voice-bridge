"""Structured logging: JSON lines with the session id, no leaking context."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from artemis.logs import JsonFormatter, TextFormatter, configure_logging, log_context


def record(msg: str = "Turn %s done", *args: object) -> logging.LogRecord:
    return logging.LogRecord("artemis-voice", logging.INFO, __file__, 1, msg, args or ("ok",), None)


def test_json_line_without_context() -> None:
    entry = json.loads(JsonFormatter("app").format(record()))
    assert entry["msg"] == "Turn ok done"
    assert (entry["level"], entry["service"], entry["logger"]) == ("info", "app", "artemis-voice")
    assert entry["ts"].endswith("+00:00")
    assert "session_id" not in entry


def test_session_id_follows_the_connection_even_after_it_changes() -> None:
    session = {"id": "aaaa1111"}
    token = log_context.set(lambda: {"session_id": session["id"]})
    try:
        assert json.loads(JsonFormatter("app").format(record()))["session_id"] == "aaaa1111"
        session["id"] = "bbbb2222"  # "new conversation"
        assert json.loads(JsonFormatter("app").format(record()))["session_id"] == "bbbb2222"
        assert "[bbbb2222]" in TextFormatter().format(record())
    finally:
        log_context.reset(token)


def test_tasks_inherit_the_context_and_others_dont_see_it() -> None:
    async def connection(sid: str) -> str:
        log_context.set(lambda: {"session_id": sid})
        task = asyncio.create_task(asyncio.sleep(0, JsonFormatter("app").format(record())))
        sid_logged: str = json.loads(await task)["session_id"]
        return sid_logged

    async def main() -> list[str]:
        return list(await asyncio.gather(connection("one"), connection("two")))

    assert asyncio.run(main()) == ["one", "two"]
    assert log_context.get() is None


def test_a_broken_context_never_breaks_logging() -> None:
    def broken() -> dict[str, str]:
        raise RuntimeError("boom")

    token = log_context.set(broken)
    try:
        assert json.loads(JsonFormatter("app").format(record()))["msg"] == "Turn ok done"
    finally:
        log_context.reset(token)


def test_configure_logging_routes_uvicorn_through_json(monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    monkeypatch.setenv("ARTEMIS_LOG_FORMAT", "json")
    try:
        configure_logging("tts")
        logging.getLogger("uvicorn.access").info("GET /health 200")
        entry = json.loads(capsys.readouterr().err.strip().splitlines()[-1])
        assert (entry["service"], entry["logger"], entry["msg"]) == ("tts", "uvicorn.access", "GET /health 200")
    finally:
        root.handlers[:], _ = saved
        root.setLevel(saved[1])
