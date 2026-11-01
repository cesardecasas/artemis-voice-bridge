"""
The agent interface. Swap the brain behind Artemis without touching the
voice pipeline: ``EchoAgent`` today, ``HermesAgent`` later.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from artemis.services import ServiceStatus
from artemis.session import Session


@dataclass(frozen=True, slots=True)
class Token:
    """A piece of the reply text, streamed as it is generated."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolStart:
    id: str
    name: str
    summary: str


@dataclass(frozen=True, slots=True)
class ToolEnd:
    id: str
    ok: bool
    summary: str


@dataclass(frozen=True, slots=True)
class Done:
    """The complete reply. Always the last event of a turn."""

    text: str


AgentEvent = Token | ToolStart | ToolEnd | Done

ApprovalRequester = Callable[[str, str], Awaitable[bool]]
"""``await request_approval(action, detail)`` -> True if the user approved."""


@dataclass(frozen=True, slots=True)
class AgentContext:
    session: Session
    request_approval: ApprovalRequester


class AgentBackend(Protocol):
    name: str

    def stream(self, ctx: AgentContext, text: str) -> AsyncGenerator[AgentEvent, None]:
        """Yield events for one user turn, ending with exactly one ``Done``.

        The pipeline may cancel the iteration at any point (user interrupt);
        implementations must clean up on ``asyncio.CancelledError``.
        """
        ...

    async def health(self) -> ServiceStatus: ...
