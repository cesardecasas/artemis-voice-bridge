"""HermesAgent against a fake Hermes Runs API (in-process httpx transport)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from artemis.agent import AgentContext, AgentEvent, Done, HermesAgent, HermesError, Token, ToolEnd, ToolStart
from artemis.agent.hermes import VOICE_INSTRUCTIONS, hermes_session_id, iter_sse, reasoning_options
from artemis.session import Session

pytestmark = pytest.mark.anyio

KEY = "test-key"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def sse(event: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(event)}\n\n".encode()


class FakeHermes:
    """Just enough of the Hermes gateway API server.

    ``script`` is the list of SSE events for the next run. A ``"WAIT_APPROVAL"``
    entry pauses the stream until ``POST /approval`` arrives; ``"HANG"`` blocks
    until the run is stopped.
    """

    def __init__(self, script: list[Any], *, up: bool = True) -> None:
        self.script = script
        self.up = up
        self.requests: list[tuple[str, str, dict[str, Any], httpx.Headers]] = []
        self.approved = asyncio.Event()
        self.stopped = asyncio.Event()

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("connection refused", request=request)
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        self.requests.append((request.method, path, body, request.headers))

        if request.headers.get("authorization") != f"Bearer {KEY}":
            return httpx.Response(401, json={"error": "unauthorized"})
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/v1/runs":
            return httpx.Response(202, json={"run_id": "run_1", "status": "started"})
        if path == "/v1/runs/run_1/events":
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, content=self.events()
            )
        if path == "/v1/runs/run_1/approval":
            self.approved.set()
            return httpx.Response(200, json={"run_id": "run_1", "choice": body.get("choice")})
        if path == "/v1/runs/run_1/clarify":
            return httpx.Response(200, json={"run_id": "run_1"})
        if path == "/v1/runs/run_1/stop":
            self.stopped.set()
            return httpx.Response(200, json={"status": "stopping"})
        return httpx.Response(404)

    async def events(self) -> AsyncIterator[bytes]:
        yield b": keep-alive\n\n"
        for item in self.script:
            if item == "WAIT_APPROVAL":
                await self.approved.wait()
            elif item == "HANG":
                await asyncio.sleep(30)
            else:
                yield sse({"run_id": "run_1", "timestamp": 0, **item})

    def calls(self, path: str) -> list[dict[str, Any]]:
        return [body for _, p, body, _ in self.requests if p == path]


class Approver:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[tuple[str, str]] = []

    async def __call__(self, action: str, detail: str) -> bool:
        self.asked.append((action, detail))
        return self.answer


def make_agent(fake: FakeHermes, key: str = KEY) -> HermesAgent:
    client = httpx.AsyncClient(transport=fake.transport())
    return HermesAgent(client, base_url="http://hermes.test", api_key=key)


async def collect(agent: HermesAgent, approver: Approver | None = None, text: str = "hi") -> list[AgentEvent]:
    ctx = AgentContext(session=Session(id="abc123"), request_approval=approver or Approver(False))
    return [event async for event in agent.stream(ctx, text)]


# ---------------------------------------------------------------- happy path


async def test_streams_tokens_tools_and_final_answer() -> None:
    fake = FakeHermes([
        {"event": "message.delta", "delta": "Let me "},
        {"event": "message.delta", "delta": "look."},
        {"event": "tool.started", "tool": "terminal", "preview": "df -h /", "tool_call_id": "c1"},
        {"event": "tool.completed", "tool": "terminal", "duration": 0.4, "error": False,
         "tool_call_id": "c1", "preview": "Filesystem  Size  Used  Avail"},
        {"event": "reasoning.available", "text": "private"},
        {"event": "run.completed", "output": "Let me look. The disk is 40% full."},
    ])
    events = await collect(make_agent(fake), text="how full is my disk?")

    assert events == [
        Token("Let me "),
        Token("look."),
        ToolStart("c1", "terminal", "df -h /"),
        ToolEnd("c1", True, "df -h /"),
        Done("Let me look. The disk is 40% full."),
    ]
    assert not fake.stopped.is_set(), "a finished run must not be stopped"


async def test_run_request_carries_session_instructions_and_auth() -> None:
    fake = FakeHermes([{"event": "run.completed", "output": "ok"}])
    await collect(make_agent(fake), text="hello")

    method, path, body, headers = fake.requests[0]
    assert (method, path) == ("POST", "/v1/runs")
    assert body == {
        "input": "hello",
        "session_id": "artemis-abc123",
        "instructions": VOICE_INSTRUCTIONS,
        "model_options": {"reasoning": {"enabled": False}},
    }
    assert headers["authorization"] == f"Bearer {KEY}"
    assert headers["x-hermes-session-key"] == "artemis:voice:abc123"
    assert headers["idempotency-key"]
    assert hermes_session_id("abc123") == "artemis-abc123"


async def test_interim_commentary_is_spoken_unless_already_streamed() -> None:
    fake = FakeHermes([
        {"event": "message.interim", "text": "Checking the logs.", "already_streamed": False},
        {"event": "message.interim", "text": "dup", "already_streamed": True},
        {"event": "run.completed", "output": "All clear."},
    ])
    events = await collect(make_agent(fake))
    assert events == [Token("Checking the logs. "), Done("All clear.")]


async def test_failed_and_blocked_tools() -> None:
    fake = FakeHermes([
        {"event": "tool.started", "tool": "terminal", "preview": "rm -rf /tmp/x"},
        {"event": "tool.completed", "tool": "terminal", "error": True, "preview": "BLOCKED: user denied"},
        {"event": "tool.started", "tool": "web_search", "preview": "weather"},
        {"event": "tool.completed", "tool": "web_search", "error": True, "preview": ""},
        {"event": "run.completed", "output": "Done."},
    ])
    events = await collect(make_agent(fake))
    ends = [e for e in events if isinstance(e, ToolEnd)]
    starts = [e for e in events if isinstance(e, ToolStart)]
    assert [e.id for e in ends] == [s.id for s in starts], "ends match starts without tool_call_id"
    assert ends[0] == ToolEnd(starts[0].id, False, "Not approved: rm -rf /tmp/x")
    assert ends[1] == ToolEnd(starts[1].id, False, "Failed: weather")


# ---------------------------------------------------------------- approvals


@pytest.mark.parametrize("answer", [True, False])
async def test_approval_is_asked_and_answered(answer: bool) -> None:
    fake = FakeHermes([
        {"event": "approval.request", "command": "systemctl restart plex",
         "description": "Restart the Plex service", "prompt_id": "p7",
         "execution_generation": 3, "choices": ["once", "session", "always", "deny"]},
        "WAIT_APPROVAL",
        {"event": "run.completed", "output": "Restarted." if answer else "Okay, I won't."},
    ])
    approver = Approver(answer)
    events = await collect(make_agent(fake), approver)

    assert approver.asked == [("Restart the Plex service", "systemctl restart plex")]
    assert fake.calls("/v1/runs/run_1/approval") == [
        {"choice": "once" if answer else "deny", "request_id": "p7", "execution_generation": 3}
    ]
    assert isinstance(events[-1], Done)


async def test_legacy_approval_without_prompt_identity() -> None:
    fake = FakeHermes([
        {"event": "approval.request", "command": "ls"},
        "WAIT_APPROVAL",
        {"event": "run.completed", "output": "ok"},
    ])
    await collect(make_agent(fake), Approver(True))
    assert fake.calls("/v1/runs/run_1/approval") == [{"choice": "once"}]


async def test_clarify_gets_a_voice_friendly_answer() -> None:
    fake = FakeHermes([
        {"event": "clarify.request", "question": "Which server?", "prompt_id": "q1", "execution_generation": 1},
        {"event": "run.completed", "output": "I assumed the main one."},
    ])
    await collect(make_agent(fake))
    [answer] = fake.calls("/v1/runs/run_1/clarify")
    assert answer["request_id"] == "q1" and "voice" in answer["answer"]


# ---------------------------------------------------------------- interrupt and failures


async def test_cancelling_the_turn_stops_the_hermes_run() -> None:
    fake = FakeHermes([{"event": "message.delta", "delta": "Working"}, "HANG"])
    agent = make_agent(fake)
    first = asyncio.Event()

    async def consume() -> None:
        ctx = AgentContext(session=Session(id="s"), request_approval=Approver(False))
        async for _ in agent.stream(ctx, "long task"):
            first.set()

    task = asyncio.create_task(consume())
    await asyncio.wait_for(first.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fake.stopped.is_set()


@pytest.mark.parametrize(
    ("event", "message"),
    [
        ({"event": "run.failed", "error": "model endpoint unreachable"}, "Hermes failed: model endpoint unreachable"),
        ({"event": "run.cancelled"}, "Hermes stopped the run"),
    ],
)
async def test_failed_runs_raise_a_readable_error(event: dict[str, Any], message: str) -> None:
    fake = FakeHermes([event])
    with pytest.raises(HermesError, match=message):
        await collect(make_agent(fake))


async def test_stream_ending_without_answer_is_an_error_and_stops_the_run() -> None:
    fake = FakeHermes([{"event": "message.delta", "delta": "half"}])
    with pytest.raises(HermesError, match="without an answer"):
        await collect(make_agent(fake))
    assert fake.stopped.is_set()


async def test_unreachable_and_bad_key() -> None:
    with pytest.raises(HermesError, match="not reachable"):
        await collect(make_agent(FakeHermes([], up=False)))
    with pytest.raises(HermesError, match="API key"):
        await collect(make_agent(FakeHermes([]), key="wrong"))


async def test_health() -> None:
    assert await make_agent(FakeHermes([])).health() == "ready"
    assert await make_agent(FakeHermes([], up=False)).health() == "offline"


# ---------------------------------------------------------------- SSE parsing


async def test_iter_sse_handles_event_lines_multiline_data_and_comments() -> None:
    async def lines() -> AsyncIterator[str]:
        for line in [
            ": ping", "",
            "event: message.delta", 'data: {"delta": "a"}', "",
            'data: {"event": "x",', 'data: "n": 1}', "",
            "data: not json", "",
            "data: [DONE]", "",
            'data: {"event": "after-done"}', "",
        ]:
            yield line

    parsed = [event async for event in iter_sse(lines())]
    assert parsed == [{"delta": "a", "event": "message.delta"}, {"event": "x", "n": 1}]


def test_reasoning_levels_map_to_model_options() -> None:
    assert reasoning_options("off") == {"reasoning": {"enabled": False}}
    assert reasoning_options("high") == {"reasoning_effort": "high"}
    assert reasoning_options("default") == {}
    with pytest.raises(ValueError, match="ARTEMIS_HERMES_REASONING"):
        reasoning_options("max")


async def test_default_reasoning_sends_no_model_options() -> None:
    fake = FakeHermes([{"event": "run.completed", "output": "ok"}])
    client = httpx.AsyncClient(transport=fake.transport())
    agent = HermesAgent(client, base_url="http://hermes.test", api_key=KEY, reasoning="default")
    await collect(agent)
    assert "model_options" not in fake.calls("/v1/runs")[0]
