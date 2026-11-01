"""In-memory conversation sessions, so a reconnecting browser keeps its context."""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Literal

Role = Literal["user", "assistant"]


@dataclass(slots=True)
class Turn:
    role: Role
    content: str
    at: float = field(default_factory=time.time)


@dataclass(slots=True)
class Session:
    id: str
    history: list[Turn] = field(default_factory=list)
    last_seen: float = field(default_factory=time.time)

    def add(self, role: Role, content: str) -> None:
        self.history.append(Turn(role=role, content=content))
        self.last_seen = time.time()


class SessionStore:
    """Keeps the most recently used sessions; the oldest are dropped first."""

    def __init__(self, max_sessions: int = 50) -> None:
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._max = max_sessions

    def get_or_create(self, session_id: str | None) -> Session:
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            self._sessions.move_to_end(session_id)
            session.last_seen = time.time()
            return session

        session = Session(id=uuid.uuid4().hex)
        self._sessions[session.id] = session

        while len(self._sessions) > self._max:
            self._sessions.popitem(last=False)

        return session

    def __len__(self) -> int:
        return len(self._sessions)
