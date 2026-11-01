"""
Logging for all three services (stdlib only: imported from every virtualenv).

``ARTEMIS_LOG_FORMAT=text`` (default, for a terminal) or ``json`` (one object
per line, used by the systemd units; journald stores and rotates them).

Log lines written while a WebSocket connection is handling something carry its
session id, via :data:`log_context`. Transcripts, replies and secrets are never
logged; only events, timings and ids.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime

LogFields = Callable[[], Mapping[str, str]]

log_context: ContextVar[LogFields | None] = ContextVar("artemis_log_context", default=None)
"""Set by a connection to a function returning its current fields (``session_id``).

A function rather than a value: tasks copy the context when they are created,
and the session can change later ("new conversation")."""

# uvicorn configures these itself; route them through our handler instead.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _context_fields() -> Mapping[str, str]:
    fields = log_context.get()
    if fields is None:
        return {}
    try:
        return fields()
    except Exception:  # a broken context must never break logging
        return {}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ts, level, service, logger, msg, session_id, exc."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "service": self.service,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update(_context_fields())
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """``LEVEL logger [session] message`` for reading in a terminal."""

    def format(self, record: logging.LogRecord) -> str:
        session = _context_fields().get("session_id")
        prefix = f"{record.levelname}:{record.name}:"
        if session:
            prefix += f" [{session[:8]}]"
        line = f"{prefix} {record.getMessage()}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure_logging(service: str, *, level: int = logging.INFO) -> None:
    """Send every log line (ours and uvicorn's) to stderr in the configured format."""
    handler = logging.StreamHandler(sys.stderr)
    if os.environ.get("ARTEMIS_LOG_FORMAT", "text").strip().lower() == "json":
        handler.setFormatter(JsonFormatter(service))
    else:
        handler.setFormatter(TextFormatter())

    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

    for name in _UVICORN_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
