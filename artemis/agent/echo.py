"""Placeholder agent that repeats what it heard. Stands in until Hermes is wired up."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

from artemis.agent.base import AgentContext, AgentEvent, Done, Token, ToolEnd, ToolStart
from artemis.services import ServiceStatus

DEMO_TRIGGER = "demo"
DEMO_TOKEN_DELAY = 0.08
DEMO_SPEECH = (
    "This is a demo of streamed speech. "
    "Each sentence is spoken as soon as it is ready, while the rest is still being written. "
    "Tap the core or press Escape to cut me off."
)


class EchoAgent:
    """Streams back ``I heard you say: ...`` word by word.

    With ``demo=True``, any message containing the word "demo" also emits a
    fake tool call, an approval request and a longer, slowly streamed reply,
    so the UI and sentence-by-sentence speech can be exercised before Hermes
    is connected.
    """

    name = "echo"

    def __init__(self, *, demo: bool = False, token_delay: float = 0.0) -> None:
        self.demo = demo
        self.token_delay = token_delay

    async def health(self) -> ServiceStatus:
        return "ready"

    async def stream(self, ctx: AgentContext, text: str) -> AsyncIterator[AgentEvent]:
        reply = f"I heard you say: {text}"
        delay = self.token_delay

        if self.demo and DEMO_TRIGGER in text.lower():
            async for event in self._demo_tools(ctx):
                yield event
            # Several sentences, generated at a readable pace, to show that
            # speech starts before the reply is finished (Phase 5).
            reply = f"{reply.rstrip('.!? ')}. {DEMO_SPEECH}"
            delay = delay or DEMO_TOKEN_DELAY

        words = reply.split(" ")

        for index, word in enumerate(words):
            yield Token(word if index == 0 else f" {word}")
            if delay:
                await asyncio.sleep(delay)

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
