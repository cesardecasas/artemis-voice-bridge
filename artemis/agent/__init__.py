"""Agent backends. Select one with ``ARTEMIS_AGENT_BACKEND``."""

from __future__ import annotations

from artemis.agent.base import (
    AgentBackend,
    AgentContext,
    AgentEvent,
    Done,
    Token,
    ToolEnd,
    ToolStart,
)
from artemis.agent.echo import EchoAgent
from artemis.config import AgentSettings

__all__ = [
    "AgentBackend",
    "AgentContext",
    "AgentEvent",
    "Done",
    "EchoAgent",
    "Token",
    "ToolEnd",
    "ToolStart",
    "create_agent",
]


def create_agent(settings: AgentSettings) -> AgentBackend:
    match settings.backend:
        case "echo":
            return EchoAgent(demo=settings.echo_demo)
        case "hermes":
            raise NotImplementedError(
                "Hermes backend arrives in Phase 6; use ARTEMIS_AGENT_BACKEND=echo"
            )
        case other:
            raise ValueError(f"Unknown ARTEMIS_AGENT_BACKEND {other!r}")
