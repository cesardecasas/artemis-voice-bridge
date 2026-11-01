"""Hermes Agent backend, over the Hermes gateway's Runs API (Phase 6).

One Artemis turn = one Hermes run:

    POST /v1/runs                     {input, session_id, instructions} -> run_id
    GET  /v1/runs/{run_id}/events     SSE: message.delta, tool.*, approval.request, run.*
    POST /v1/runs/{run_id}/approval   answer an approval.request (once | deny)
    POST /v1/runs/{run_id}/stop       interrupt / barge-in

Hermes keeps the conversation server-side under ``session_id``, so Artemis only
sends the new user message. See docs/hermes.md.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from typing import Any

import httpx

from artemis.agent.base import AgentContext, AgentEvent, Done, Token, ToolEnd, ToolStart
from artemis.services import ServiceStatus

logger = logging.getLogger("artemis.hermes")

VOICE_INSTRUCTIONS = """\
You are talking to the user through Artemis, a voice interface. Everything \
you write outside code blocks is read aloud by a text-to-speech voice.
- Answer in short, natural spoken sentences. One to three sentences is ideal; \
offer to go into more detail instead of giving it unasked.
- No markdown tables, headings, bullet lists or emoji in spoken text.
- Put code, commands, file contents, logs and long output in fenced code \
blocks. They are shown on screen but never spoken, so say briefly what they \
contain ("Here's the script.").
- Say numbers, units and abbreviations the way a person would say them out loud.
- Reply in the language the user spoke (English or Spanish); the voice switches to match.
- Before a long-running tool call, say in a few words what you are about to do.
"""

CLARIFY_ANSWER = (
    "The user is talking by voice and cannot answer clarification prompts right "
    "now. Make a reasonable assumption, say what you assumed, and continue."
)

REASONING_LEVELS = ("off", "low", "medium", "high", "default")


def reasoning_options(level: str) -> dict[str, Any]:
    """Per-request ``model_options`` for a reasoning level (``default`` = Hermes config).

    Voice wants a fast first word: hidden reasoning before the answer is silence.
    """
    match level:
        case "off":
            return {"reasoning": {"enabled": False}}
        case "low" | "medium" | "high":
            return {"reasoning_effort": level}
        case "default":
            return {}
        case other:
            raise ValueError(f"ARTEMIS_HERMES_REASONING must be one of {REASONING_LEVELS}, not {other!r}")


CONNECT_TIMEOUT_S = 5.0
REQUEST_TIMEOUT_S = 15.0
# The event stream stays open for the whole run (tools, approvals), so no read timeout.
STREAM_TIMEOUT = httpx.Timeout(REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S, read=None)
STOP_TIMEOUT_S = 5.0
SUMMARY_MAX_CHARS = 140


class HermesError(RuntimeError):
    """A Hermes run failed. ``str(error)`` is safe to show users."""


def _clip(text: str, limit: int = SUMMARY_MAX_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def hermes_session_id(artemis_session_id: str) -> str:
    """The Hermes session that holds this Artemis conversation."""
    return f"artemis-{artemis_session_id}"


async def iter_sse(lines: AsyncIterator[str]) -> AsyncIterator[dict[str, Any]]:
    """Parse a Server-Sent Events stream into JSON payloads.

    Hermes puts the event name inside the JSON (``{"event": "message.delta", ...}``);
    an ``event:`` line, if present, is used as a fallback. Comments (``:``) are
    keep-alives and are skipped.
    """
    data: list[str] = []
    name = ""
    async for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data:
                payload = "\n".join(data)
                data, event_name = [], name
                name = ""
                if payload.strip() == "[DONE]":
                    return
                try:
                    parsed = json.loads(payload)
                except json.JSONDecodeError:
                    logger.warning("Ignoring non-JSON SSE payload: %.80s", payload)
                    continue
                if isinstance(parsed, dict):
                    if event_name and "event" not in parsed:
                        parsed["event"] = event_name
                    yield parsed
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "data":
            data.append(value)
        elif field == "event":
            name = value


class HermesAgent:
    """Streams one Hermes run per user turn and maps its events to Artemis events."""

    name = "hermes"

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        api_key: str,
        instructions: str = "",
        reasoning: str = "off",
    ) -> None:
        self._client = client
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.instructions = instructions or VOICE_INSTRUCTIONS
        self.model_options = reasoning_options(reasoning)

    # ---------------------------------------------- helpers

    def _headers(self, session_key: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        if session_key:
            # Stable long-term-memory scope for this conversation.
            headers["X-Hermes-Session-Key"] = session_key
        return headers

    async def _post(self, path: str, body: Mapping[str, Any], **kw: Any) -> dict[str, Any]:
        try:
            response = await self._client.post(
                f"{self.base_url}{path}",
                json=body,
                timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
                **kw,
            )
        except httpx.HTTPError as error:
            raise HermesError(
                "Hermes is not reachable. Is its API server enabled? (see docs/hermes.md)"
            ) from error
        if response.status_code in (401, 403):
            raise HermesError("Hermes rejected the API key (check ARTEMIS_HERMES_KEY)")
        if response.is_error:
            logger.error("Hermes %s -> %d: %.300s", path, response.status_code, response.text)
            raise HermesError(f"Hermes returned an error ({response.status_code})")
        try:
            result = response.json()
        except ValueError:
            result = {}
        return result if isinstance(result, dict) else {}

    # ---------------------------------------------- AgentBackend

    async def health(self) -> ServiceStatus:
        try:
            response = await self._client.get(
                f"{self.base_url}/health", headers=self._headers(), timeout=2.0
            )
        except httpx.HTTPError:
            return "offline"
        return "ready" if response.is_success else "offline"

    async def stream(self, ctx: AgentContext, text: str) -> AsyncGenerator[AgentEvent, None]:
        session_id = hermes_session_id(ctx.session.id)
        run: dict[str, Any] = {
            "input": text,
            "session_id": session_id,
            "instructions": self.instructions,
        }
        if self.model_options:
            run["model_options"] = self.model_options
        created = await self._post(
            "/v1/runs",
            run,
            headers={
                **self._headers(f"artemis:voice:{ctx.session.id}"),
                "Idempotency-Key": uuid.uuid4().hex,
            },
        )
        run_id = str(created.get("run_id", ""))
        if not run_id:
            raise HermesError("Hermes did not start a run")
        logger.info("Hermes run %s started (session %s)", run_id, session_id)

        finished = False
        try:
            async for event in self._run_events(ctx, run_id):
                if isinstance(event, Done):
                    finished = True
                yield event
            if not finished:
                raise HermesError("Hermes ended the run without an answer")
        finally:
            if not finished:
                # Interrupt, barge-in or failure: make Hermes stop working too.
                # Shielded so a cancelled turn still delivers the stop request.
                stop = asyncio.ensure_future(self._stop(run_id))
                try:
                    await asyncio.shield(stop)
                except BaseException:  # noqa: BLE001 - cancellation must not skip the stop
                    pass

    async def _stop(self, run_id: str) -> None:
        try:
            await self._client.post(
                f"{self.base_url}/v1/runs/{run_id}/stop",
                headers=self._headers(),
                timeout=STOP_TIMEOUT_S,
            )
            logger.info("Hermes run %s stopped", run_id)
        except httpx.HTTPError as error:
            logger.warning("Could not stop Hermes run %s: %r", run_id, error)

    async def _run_events(self, ctx: AgentContext, run_id: str) -> AsyncIterator[AgentEvent]:
        tools: dict[str, tuple[str, str]] = {}  # id -> (name, summary)
        streamed: list[str] = []

        try:
            async with self._client.stream(
                "GET",
                f"{self.base_url}/v1/runs/{run_id}/events",
                headers={**self._headers(), "Accept": "text/event-stream"},
                timeout=STREAM_TIMEOUT,
            ) as response:
                if response.is_error:
                    raise HermesError(f"Hermes event stream failed ({response.status_code})")

                async for event in iter_sse(response.aiter_lines()):
                    kind = str(event.get("event", ""))
                    match kind:
                        case "message.delta":
                            delta = str(event.get("delta", ""))
                            if delta:
                                streamed.append(delta)
                                yield Token(delta)

                        case "message.interim":
                            # Commentary next to tool calls ("Let me check that.").
                            interim = str(event.get("text", "")).strip()
                            if interim and not event.get("already_streamed"):
                                chunk = f"{interim} " if not streamed else f" {interim} "
                                streamed.append(chunk)
                                yield Token(chunk)

                        case "tool.started":
                            tool_id = str(event.get("tool_call_id") or uuid.uuid4().hex[:8])
                            name = str(event.get("tool", "tool"))
                            summary = _clip(str(event.get("preview") or "")) or f"Running {name}"
                            tools[tool_id] = (name, summary)
                            yield ToolStart(tool_id, name, summary)

                        case "tool.completed":
                            yield self._tool_end(event, tools)

                        case "approval.request":
                            await self._handle_approval(ctx, run_id, event)

                        case "clarify.request":
                            await self._answer_clarify(run_id, event)

                        case "run.completed":
                            output = str(event.get("output") or "".join(streamed)).strip()
                            yield Done(output)
                            return

                        case "run.failed":
                            error = _clip(str(event.get("error") or "unknown error"), 200)
                            raise HermesError(f"Hermes failed: {error}")

                        case "run.cancelled" | "run.interrupted":
                            raise HermesError("Hermes stopped the run")

                        case _:
                            logger.debug("Hermes event ignored: %s", kind)
        except httpx.HTTPError as error:
            raise HermesError("Lost the connection to Hermes") from error

    @staticmethod
    def _tool_end(event: Mapping[str, Any], tools: dict[str, tuple[str, str]]) -> ToolEnd:
        tool_id = str(event.get("tool_call_id") or "")
        name = str(event.get("tool") or "tool")
        if not tool_id:
            # Without ids, match the most recent running tool of the same name.
            tool_id = next(
                (key for key, (running, _) in reversed(tools.items()) if running == name),
                uuid.uuid4().hex[:8],
            )
        _, started = tools.pop(tool_id, (name, ""))
        failed = bool(event.get("error"))
        preview = _clip(str(event.get("preview") or ""))
        # Keep the card about *what* ran (e.g. "df -h /"); the UI adds ✓/✗ and duration.
        if preview.startswith("BLOCKED"):
            summary = f"Not approved: {started}" if started else "Not approved"
        elif failed:
            summary = f"Failed: {started}" if started else (preview or f"{name} failed")
        else:
            summary = started or preview or f"{name} finished"
        return ToolEnd(tool_id, not failed, summary)

    async def _handle_approval(
        self, ctx: AgentContext, run_id: str, event: Mapping[str, Any]
    ) -> None:
        command = str(event.get("command") or event.get("detail") or "")
        action = str(event.get("description") or "Hermes wants to run a command")
        approved = await ctx.request_approval(_clip(action, 160), command)

        body: dict[str, Any] = {"choice": "once" if approved else "deny"}
        # Canonical gateway: exact prompt identity required. Legacy: request_id optional.
        request_id = event.get("prompt_id") or event.get("request_id")
        if request_id:
            body["request_id"] = str(request_id)
        if event.get("execution_generation") is not None:
            body["execution_generation"] = event["execution_generation"]
        await self._post(f"/v1/runs/{run_id}/approval", body, headers=self._headers())
        logger.info("Hermes run %s approval: %s", run_id, body["choice"])

    async def _answer_clarify(self, run_id: str, event: Mapping[str, Any]) -> None:
        body: dict[str, Any] = {"answer": CLARIFY_ANSWER}
        request_id = event.get("prompt_id") or event.get("request_id")
        if request_id:
            body["request_id"] = str(request_id)
        if event.get("execution_generation") is not None:
            body["execution_generation"] = event["execution_generation"]
        await self._post(f"/v1/runs/{run_id}/clarify", body, headers=self._headers())
