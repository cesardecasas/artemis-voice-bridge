"""Voice activity detection for hands-free mode (Phase 7).

``SpeechSegmenter`` turns a continuous 16 kHz mono stream into speech
segments: it starts a segment when speech begins (with a short pre-roll so the
first syllable isn't clipped) and ends it after a stretch of silence.

Speech probabilities come from a ``VadModel``: ``SileroVad`` (Silero VAD v5,
ONNX, CPU, about 0.1 ms per 32 ms chunk) in production, or a fake in tests.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from numpy.typing import NDArray

SAMPLE_RATE = 16000
CHUNK = 512  # samples per VAD step at 16 kHz (32 ms), fixed by Silero v5
CHUNK_MS = CHUNK * 1000 // SAMPLE_RATE

Samples = NDArray[np.float32]


class VadStream(Protocol):
    """Per-connection VAD state."""

    def probability(self, chunk: Samples) -> float:
        """Speech probability (0–1) of one ``CHUNK``-sample float32 chunk."""
        ...


class VadModel(Protocol):
    def stream(self) -> VadStream: ...


class SileroVad:
    """Silero VAD v5. One ONNX session shared by all connections."""

    CONTEXT = 64  # v5 prepends the tail of the previous chunk

    def __init__(self, model_path: Path) -> None:
        import onnxruntime as ort  # imported lazily: only needed when hands-free is used

        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)

    def stream(self) -> _SileroStream:
        return _SileroStream(self)

    def run(self, audio: Samples, state: NDArray[Any]) -> tuple[float, NDArray[Any]]:
        out, new_state = self._session.run(
            None, {"input": audio[np.newaxis, :], "state": state, "sr": self._sr}
        )
        return float(out[0][0]), new_state


class _SileroStream:
    def __init__(self, model: SileroVad) -> None:
        self._model = model
        self._state: NDArray[Any] = np.zeros((2, 1, 128), dtype=np.float32)
        self._context: Samples = np.zeros(SileroVad.CONTEXT, dtype=np.float32)

    def probability(self, chunk: Samples) -> float:
        audio = np.concatenate([self._context, chunk])
        self._context = chunk[-SileroVad.CONTEXT :]
        prob, self._state = self._model.run(audio, self._state)
        return prob


@dataclass(frozen=True, slots=True)
class VadConfig:
    threshold: float = 0.5
    """Probability that starts speech."""
    neg_threshold: float = 0.35
    """Probability below which a chunk counts as silence (hysteresis)."""
    min_speech_ms: int = 250
    """Shorter blips (a cough, a click) are dropped."""
    min_silence_ms: int = 700
    """This much silence ends a segment."""
    pre_roll_ms: int = 300
    """Audio kept from before speech started."""
    max_segment_s: float = 30.0


@dataclass(slots=True)
class SpeechSegment:
    audio: Samples
    """float32 samples at 16 kHz, pre-roll included."""

    @property
    def seconds(self) -> float:
        return len(self.audio) / SAMPLE_RATE


@dataclass
class SpeechSegmenter:
    """Feed PCM; get back finished speech segments."""

    vad: VadStream
    config: VadConfig = field(default_factory=VadConfig)
    on_speech_start: Any = None
    """Optional zero-argument callback when speech begins (for UI feedback)."""

    def __post_init__(self) -> None:
        self._pending: Samples = np.zeros(0, dtype=np.float32)
        self._pre_roll: deque[Samples] = deque(maxlen=max(1, self.config.pre_roll_ms // CHUNK_MS))
        self._speech: list[Samples] = []
        self._speech_chunks = 0
        self._silence_chunks = 0
        self._in_speech = False

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def reset(self) -> None:
        self.__post_init__()

    def feed_pcm16(self, data: bytes) -> list[SpeechSegment]:
        """Little-endian 16-bit mono PCM at 16 kHz (what the browser streams)."""
        if len(data) % 2:
            data = data[:-1]
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        return self.feed(samples)

    def feed(self, samples: Samples) -> list[SpeechSegment]:
        self._pending = np.concatenate([self._pending, samples])
        segments: list[SpeechSegment] = []
        while len(self._pending) >= CHUNK:
            chunk, self._pending = self._pending[:CHUNK], self._pending[CHUNK:]
            segment = self._step(chunk)
            if segment is not None:
                segments.append(segment)
        return segments

    def _step(self, chunk: Samples) -> SpeechSegment | None:
        prob = self.vad.probability(chunk)
        cfg = self.config

        if not self._in_speech:
            if prob >= cfg.threshold:
                self._in_speech = True
                self._speech = [*self._pre_roll, chunk]
                self._pre_roll.clear()
                self._speech_chunks = 1
                self._silence_chunks = 0
                if self.on_speech_start is not None:
                    self.on_speech_start()
            else:
                self._pre_roll.append(chunk)
            return None

        self._speech.append(chunk)
        if prob < cfg.neg_threshold:
            self._silence_chunks += 1
        else:
            self._silence_chunks = 0
            self._speech_chunks += 1

        too_long = len(self._speech) * CHUNK_MS >= cfg.max_segment_s * 1000
        if self._silence_chunks * CHUNK_MS < cfg.min_silence_ms and not too_long:
            return None

        # End of segment: trim most of the trailing silence, keep ~200 ms.
        keep_tail = max(0, self._silence_chunks - 200 // CHUNK_MS)
        chunks = self._speech[: len(self._speech) - keep_tail] if keep_tail else self._speech
        long_enough = self._speech_chunks * CHUNK_MS >= cfg.min_speech_ms
        self._in_speech = False
        self._speech = []
        self._silence_chunks = 0
        if not long_enough:
            return None
        return SpeechSegment(np.concatenate(chunks))


def to_wav(samples: Samples) -> bytes:
    """16-bit mono WAV, for the STT service (it decodes WAV as well as WebM)."""
    import io
    import wave

    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return buffer.getvalue()
