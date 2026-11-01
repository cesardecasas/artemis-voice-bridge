"""Placeholder agent that repeats what it heard. Stands in until Hermes is wired up."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from artemis.agent.base import AgentContext, AgentEvent, Done, Token, ToolEnd, ToolStart
from artemis.services import ServiceStatus

DEMO_TRIGGER = "demo"


class EchoAgent:
    """Streams back ``I heard you say: ...`` word by word.

    With ``demo=True``, any message containing the word "demo" also emits a
    fake tool call and an approval request, so the UI for those events can be
    exercised before Hermes is connected.
    """

    name = "echo"

    def __init__(self, *, demo: bool = False, token_delay: float = 0.0) -> None:
        self.demo = demo
        self.token_delay = token_delay

    async def health(self) -> ServiceStatus:
        return "ready"

    async def stream(self, ctx: AgentContext, text: str) -> AsyncIterator[AgentEvent]:
        if self.demo and DEMO_TRIGGER in text.lower():
            async for event in self._demo_tools(ctx):
                yield event

        reply = f"I heard you say: {text}"
        words = reply.split(" ")

        for index, word in enumerate(words):
            yield Token(word if index == 0 else f" {word}")
            if self.token_delay:
                await asyncio.sleep(self.token_delay)

        yield Done(reply)

    async def _demo_tools(self, ctx: AgentContext) -> AsyncIterator[AgentEvent]:
        tool_id = uuid.uuid4().hex[:8]
        yield ToolStart(tool_id, "system.check", "Checking local services")
        await asyncio.sleep(0.4)
        yield ToolEnd(tool_id, True, "All services healthy")

        approved = await ctx.request_approval(
            "Run demo command", "echo 'hello from Artemis'"
        )
        yield Token("(Demo command approved.) " if approved else "(Demo command denied.) ")
