"""Hands-free mode: VAD segmentation, wake word, controller, and the /ws flow."""

from __future__ import annotations

import asyncio
import io
import wave
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from artemis.handsfree import HandsFree, HandsFreeConfig
from artemis.protocol import HandsFreeStatus
from artemis.vad import CHUNK, SAMPLE_RATE, Samples, SileroVad, SpeechSegmenter, VadConfig, to_wav
from artemis.wake import match_approval, match_wake

# ---------------------------------------------------------------- fake audio + VAD


class LoudnessVad:
    """Speech = anything louder than near-silence. Deterministic stand-in for Silero."""

    def stream(self) -> LoudnessVad:
        return self

    def probability(self, chunk: Samples) -> float:
        return 1.0 if float(np.abs(chunk).mean()) > 0.01 else 0.0


def tone(ms: int) -> Samples:
    t = np.arange(SAMPLE_RATE * ms // 1000) / SAMPLE_RATE
    return (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def silence(ms: int) -> Samples:
    return np.zeros(SAMPLE_RATE * ms // 1000, dtype=np.float32)


def pcm16(samples: Samples) -> bytes:
    return (samples * 32767).astype("<i2").tobytes()


# ---------------------------------------------------------------- segmenter


def test_segment_ends_after_silence_with_pre_roll() -> None:
    seg = SpeechSegmenter(LoudnessVad(), VadConfig(min_silence_ms=700, pre_roll_ms=300))
    assert seg.feed(silence(1000)) == []
    assert seg.feed(tone(1000)) == []
    speaking = seg.in_speech
    assert seg.feed(silence(400)) == [], "a short pause doesn't end the utterance"
    [segment] = seg.feed(silence(500))
    ended = not seg.in_speech
    assert speaking and ended
    # 1 s speech + ~300 ms pre-roll + ~200 ms of kept tail silence
    assert 1.3 <= segment.seconds <= 1.6


def test_blips_shorter_than_min_speech_are_dropped() -> None:
    seg = SpeechSegmenter(LoudnessVad(), VadConfig(min_speech_ms=250))
    assert seg.feed(np.concatenate([tone(100), silence(1000)])) == []


def test_long_speech_is_cut_at_max_length() -> None:
    seg = SpeechSegmenter(LoudnessVad(), VadConfig(max_segment_s=2.0))
    segments = seg.feed(tone(5000))
    assert len(segments) == 2
    assert all(s.seconds <= 2.1 for s in segments)


def test_pcm16_frames_of_any_size() -> None:
    seg = SpeechSegmenter(LoudnessVad())
    data = pcm16(np.concatenate([tone(800), silence(1000)]))
    segments = []
    for start in range(0, len(data), 1234):  # odd sizes, split samples
        segments += seg.feed_pcm16(data[start : start + 1234])
    assert len(segments) == 1


def test_to_wav_roundtrip() -> None:
    wav = to_wav(tone(500))
    with wave.open(io.BytesIO(wav)) as reader:
        assert (reader.getframerate(), reader.getnchannels(), reader.getsampwidth()) == (16000, 1, 2)
        assert reader.getnframes() == 8000


SILERO = Path(__file__).resolve().parent.parent / "models" / "silero" / "silero_vad.onnx"


@pytest.mark.skipif(not SILERO.is_file(), reason="Silero model not downloaded")
def test_real_silero_ignores_silence_and_noise() -> None:
    # (A steady low tone does read as voice-like to Silero, so it isn't used here.
    # Checked separately against Kokoro speech: 91% of chunks > 0.5.)
    stream = SileroVad(SILERO).stream()
    noise = (np.random.default_rng(0).standard_normal(CHUNK * 30) * 0.05).astype(np.float32)
    probs = [stream.probability(chunk) for chunk in np.split(noise, 30)]
    probs.append(stream.probability(np.zeros(CHUNK, dtype=np.float32)))
    assert max(probs) < 0.3


# ---------------------------------------------------------------- wake word + approvals


@pytest.mark.parametrize(
    ("text", "command"),
    [
        ("Artemis, what time is it?", "what time is it?"),
        ("Hey Artemis turn off the lights.", "turn off the lights."),
        ("OK, Artemus, play music", "play music"),
        ("Arthemis.", ""),
        ("artemis", ""),
    ],
)
def test_wake_word_matches(text: str, command: str) -> None:
    match = match_wake(text)
    assert match is not None and match.command == command


@pytest.mark.parametrize(
    "text",
    ["The Artemis mission launched", "Art is fun", "Armistice day", "Hermes, do it", "Artemis's car", ""],
)
def test_wake_word_rejects(text: str) -> None:
    assert match_wake(text) is None


def test_custom_wake_word() -> None:
    assert match_wake("Jarvis, lights", "jarvis") is not None
    assert match_wake("Artemis, lights", "jarvis") is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Approve.", True), ("Yes, go ahead", True), ("Artemis, deny", False), ("No.", False),
     ("Cancel that", False), ("What does it do?", None)],
)
def test_spoken_approvals(text: str, expected: bool | None) -> None:
    assert match_approval(text) is expected


# ---------------------------------------------------------------- controller


class FakeHost:
    def __init__(self, transcripts: list[str]) -> None:
        self.transcripts = transcripts
        self.statuses: list[tuple[HandsFreeStatus, str]] = []
        self.turns: list[str] = []
        self.interrupts = 0
        self.busy = False
        self.pending_approval = False
        self.approvals: list[bool] = []

    async def transcribe_segment(self, wav: bytes) -> str:
        assert wav[:4] == b"RIFF"
        return self.transcripts.pop(0) if self.transcripts else ""

    async def send_handsfree(self, status: HandsFreeStatus, reason: str) -> None:
        self.statuses.append((status, reason))

    def turn_active(self) -> bool:
        return self.busy

    def approval_pending(self) -> bool:
        return self.pending_approval

    async def resolve_approval_by_voice(self, approved: bool) -> None:
        self.approvals.append(approved)
        self.pending_approval = False

    async def start_voice_turn(self, text: str, stt_ms: int) -> None:
        self.turns.append(text)

    async def interrupt_turn(self) -> None:
        self.interrupts += 1
        self.busy = False


UTTERANCE = pcm16(np.concatenate([tone(600), silence(900)]))


async def say(hands: HandsFree, times: int = 1) -> None:
    for _ in range(times):
        hands.feed(UTTERANCE)
    for _ in range(50):  # let the worker drain the queue
        await asyncio.sleep(0)
        if hands._queue.empty():
            await asyncio.sleep(0.01)
            if hands._queue.empty():
                return


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make(host: FakeHost, **config: Any) -> HandsFree:
    return HandsFree(host, LoudnessVad(), HandsFreeConfig(**config))


@pytest.mark.anyio
async def test_wake_word_and_command_in_one_sentence() -> None:
    host = FakeHost(["Artemis, what's on my calendar?"])
    hands = make(host)
    await say(hands)
    assert host.turns == ["what's on my calendar?"]
    await hands.close()


@pytest.mark.anyio
async def test_speech_without_wake_word_is_ignored() -> None:
    host = FakeHost(["so I told him about the game", "pass the salt"])
    hands = make(host)
    await say(hands, 2)
    assert host.turns == []
    await hands.close()


@pytest.mark.anyio
async def test_wake_word_alone_then_command() -> None:
    host = FakeHost(["Artemis.", "turn on the lights"])
    hands = make(host)
    await say(hands)
    assert host.statuses[-1] == ("listening", "wake")
    await say(hands)
    assert host.turns == ["turn on the lights"]
    assert host.statuses[-1] == ("armed", "turn")
    await hands.close()


@pytest.mark.anyio
async def test_follow_up_window_needs_no_wake_word_then_expires() -> None:
    host = FakeHost(["and tomorrow?", "something else"])
    hands = make(host, follow_up_s=0.05)
    await hands.playback_finished()
    assert host.statuses[-1] == ("listening", "follow_up")
    await say(hands)
    assert host.turns == ["and tomorrow?"]

    await hands.playback_finished()
    await asyncio.sleep(1.2)  # window (0.05 s) plus the 1 s grace has passed
    assert host.statuses[-1] == ("armed", "timeout")
    await say(hands)
    assert host.turns == ["and tomorrow?"], "window closed: wake word needed again"
    await hands.close()


@pytest.mark.anyio
async def test_follow_up_ignores_whisper_hallucinations() -> None:
    host = FakeHost(["Thank you."])
    hands = make(host)
    await hands.playback_finished()
    await say(hands)
    assert host.turns == []
    await hands.close()


@pytest.mark.anyio
async def test_while_busy_the_wake_word_is_required_and_stop_interrupts() -> None:
    host = FakeHost(["I am Artemis, your assistant", "Artemis, stop."])
    host.busy = True
    hands = make(host)
    await say(hands)
    assert host.interrupts == 0 and host.turns == [], "its own voice leaking back is ignored"
    await say(hands)
    assert host.interrupts == 1 and host.turns == []
    await hands.close()


@pytest.mark.anyio
async def test_wake_word_with_new_question_barges_in() -> None:
    host = FakeHost(["Artemis, actually, what about Friday?"])
    host.busy = True
    hands = make(host)
    await say(hands)
    assert host.turns == ["actually, what about Friday?"]
    await hands.close()


@pytest.mark.anyio
async def test_spoken_approval_resolves_the_pending_card() -> None:
    host = FakeHost(["Yes, do it."])
    host.busy = True
    host.pending_approval = True
    hands = make(host)
    await say(hands)
    assert host.approvals == [True]
    assert host.turns == []
    await hands.close()


@pytest.mark.anyio
@pytest.mark.parametrize("phrase", ["Stop.", "stop", "Okay, stop.", "Please stop", "Stop talking.", "Enough.", "Be quiet!"])
async def test_bare_stop_while_speaking_needs_no_wake_word(phrase: str) -> None:
    host = FakeHost([phrase])
    host.busy = True  # e.g. the browser is still playing a long reply
    hands = make(host)
    await say(hands)
    assert host.interrupts == 1 and host.turns == []
    assert host.statuses[-1] == ("armed", "stopped")
    await hands.close()


@pytest.mark.anyio
@pytest.mark.parametrize("phrase", ["Thank you.", "Thanks.", "We should stop the launch now", "Don't stop."])
async def test_not_a_bare_stop(phrase: str) -> None:
    host = FakeHost([phrase])
    host.busy = True
    hands = make(host)
    await say(hands)
    assert host.interrupts == 0 and host.turns == []
    await hands.close()


@pytest.mark.anyio
@pytest.mark.parametrize("phrase", ["Artemis, stop.", "Artemis, thanks.", "Artemis, that's enough"])
async def test_stop_with_wake_word_is_never_sent_to_the_agent(phrase: str) -> None:
    # The bug seen live: the reply was still playing but the server turn had
    # ended, so "Artemis, stop." became a new question ("Understood. Standing by.").
    host = FakeHost([phrase])
    hands = make(host)  # not busy
    await say(hands)
    assert host.turns == []
    assert host.interrupts == 1
    await hands.close()
