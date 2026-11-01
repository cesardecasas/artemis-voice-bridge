"""Agent backends. Select one with ``ARTEMIS_AGENT_BACKEND``."""

from __future__ import annotations

import httpx

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
from artemis.agent.hermes import HermesAgent, HermesError
from artemis.config import AgentSettings

__all__ = [
    "AgentBackend",
    "AgentContext",
    "AgentEvent",
    "Done",
    "EchoAgent",
    "HermesAgent",
    "HermesError",
    "Token",
    "ToolEnd",
    "ToolStart",
    "create_agent",
]


def create_agent(settings: AgentSettings, client: httpx.AsyncClient) -> AgentBackend:
    """Build the configured backend. ``client`` is the app's shared HTTP client."""
    match settings.backend:
        case "echo":
            return EchoAgent(demo=settings.echo_demo)
        case "hermes":
            return HermesAgent(
                client,
                base_url=settings.hermes_url,
                api_key=settings.hermes_key,
                instructions=settings.hermes_instructions,
                reasoning=settings.hermes_reasoning,
            )
        case other:
            raise ValueError(f"Unknown ARTEMIS_AGENT_BACKEND {other!r}")
