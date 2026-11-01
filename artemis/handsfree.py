"""Hands-free listening: VAD segments → wake word → turns (Phase 7).

The browser streams 16 kHz PCM continuously while hands-free is on. Each
connection gets a ``HandsFree`` controller:

    armed      Waiting for the wake word. Every speech segment is transcribed
               and ignored unless it starts with "Artemis".
    listening  No wake word needed. Entered when the wake word is said alone
               ("Artemis." → chime → "what's the weather?"), and for a
               follow-up window after Artemis finishes speaking.

While a turn is running (Artemis thinking or speaking), the wake word is
required again, so Artemis's own voice leaking into the mic can't trigger
anything. "Artemis, stop" interrupts; "Artemis, <new question>" barges in.
While an approval card is open, "approve" / "deny" (with or without the wake
word) answers it.

Segments are transcribed in memory and discarded; text without the wake word
is never logged.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from artemis.protocol import HandsFreeStatus
from artemis.vad import SpeechSegment, SpeechSegmenter, VadConfig, VadStream, to_wav
from artemis.wake import WAKE_WORD, match_approval, match_wake

logger = logging.getLogger("artemis.handsfree")

_STOP_COMMAND = re.compile(
    r"^(stop|cancel|quiet|be quiet|shut up|enough|never ?mind|that's enough|thanks?|thank you)\W*$",
    re.IGNORECASE,
)
MAX_QUEUED_SEGMENTS = 4

# What Whisper "hears" in near-silence or noise. Only filtered when no wake word
# was needed (follow-up window); "Artemis, thank you" still goes through.
_HALLUCINATIONS = frozenset(
    {"thank you", "thanks", "thank you for watching", "thanks for watching", "you", "bye",
     "okay", "ok", "so", "yeah", "hmm", "uh", "um"}
)


@dataclass(frozen=True, slots=True)
class HandsFreeConfig:
    wake_word: str = WAKE_WORD
    follow_up_s: float = 8.0
    """After Artemis finishes speaking, how long a follow-up needs no wake word."""
    listen_s: float = 8.0
    """After the wake word is said alone, how long to wait for the command."""
    vad: VadConfig = VadConfig()


class HandsFreeHost(Protocol):
    """What the controller needs from the connection."""

    async def transcribe_segment(self, wav: bytes) -> str: ...
    async def send_handsfree(self, status: HandsFreeStatus, reason: str) -> None: ...
    def turn_active(self) -> bool: ...
    def approval_pending(self) -> bool: ...
    async def resolve_approval_by_voice(self, approved: bool) -> None: ...
    async def start_voice_turn(self, text: str, stt_ms: int) -> None: ...
    async def interrupt_turn(self) -> None: ...


class HandsFree:
    def __init__(
        self,
        host: HandsFreeHost,
        vad: VadStream,
        config: HandsFreeConfig = HandsFreeConfig(),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._host = host
        self.config = config
        self._clock = clock
        self._segmenter = SpeechSegmenter(vad, config.vad)
        self._queue: asyncio.Queue[SpeechSegment] = asyncio.Queue()
        self.status: HandsFreeStatus = "armed"
        self._listen_until = 0.0
        self._expiry: asyncio.TimerHandle | None = None
        self._worker = asyncio.create_task(self._work())

    # ---------------------------------------------- input

    def feed(self, pcm16: bytes) -> None:
        """Called from the receive loop for each binary PCM frame. Never blocks."""
        for segment in self._segmenter.feed_pcm16(pcm16):
            if self._queue.qsize() >= MAX_QUEUED_SEGMENTS:
                self._queue.get_nowait()  # fall behind gracefully: drop the oldest
                logger.warning("Hands-free is behind; dropped a speech segment")
            self._queue.put_nowait(segment)

    async def playback_finished(self) -> None:
        """The client finished playing a reply: open the follow-up window."""
        if not self._host.turn_active():
            await self._listen(self.config.follow_up_s, "follow_up")

    async def close(self) -> None:
        if self._expiry is not None:
            self._expiry.cancel()
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass

    # ---------------------------------------------- state

    async def _set(self, status: HandsFreeStatus, reason: str) -> None:
        self.status = status
        await self._host.send_handsfree(status, reason)

    async def _listen(self, seconds: float, reason: str) -> None:
        self._listen_until = self._clock() + seconds
        if self._expiry is not None:
            self._expiry.cancel()
        self._expiry = asyncio.get_running_loop().call_later(seconds, self._expire)
        await self._set("listening", reason)

    def _expire(self) -> None:
        # Don't close the window on someone mid-sentence; the segment will decide.
        if self.status == "listening" and not self._segmenter.in_speech and self._queue.empty():
            asyncio.ensure_future(self._set("armed", "timeout"))

    def _listening(self) -> bool:
        return self.status == "listening" and self._clock() <= self._listen_until + 1.0

    # ---------------------------------------------- segments

    async def _work(self) -> None:
        while True:
            segment = await self._queue.get()
            try:
                await self._handle(segment)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Hands-free segment failed")

    async def _handle(self, segment: SpeechSegment) -> None:
        listening = self._listening()  # decided by when speech ended, not by STT latency
        started = time.perf_counter()
        text = (await self._host.transcribe_segment(to_wav(segment.audio))).strip()
        stt_ms = int((time.perf_counter() - started) * 1000)
        if not text:
            return

        if self._host.approval_pending():
            decision = match_approval(text)
            if decision is not None:
                logger.info("Approval answered by voice: %s", "approve" if decision else "deny")
                await self._host.resolve_approval_by_voice(decision)
                return

        wake = match_wake(text, self.config.wake_word)
        busy = self._host.turn_active()

        if wake is None and not (listening and not busy):
            logger.debug("Ignored a %.1f s segment without the wake word", segment.seconds)
            return

        command = wake.command if wake is not None else text
        if wake is None and _normalized(command) in _HALLUCINATIONS and segment.seconds < 2.0:
            logger.debug("Ignored a likely Whisper hallucination in the follow-up window")
            return
        if busy and _STOP_COMMAND.match(command):
            logger.info("Stopped by voice")
            await self._host.interrupt_turn()
            await self._set("armed", "stopped")
            return

        if not command:
            # "Artemis." on its own: stop whatever is happening and wait for the command.
            if busy:
                await self._host.interrupt_turn()
            await self._listen(self.config.listen_s, "wake")
            return

        await self._set("armed", "turn")
        await self._host.start_voice_turn(command, stt_ms)


def _normalized(text: str) -> str:
    return " ".join(re.findall(r"[a-z']+", text.lower()))
