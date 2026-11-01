#!/usr/bin/env python3
"""
Artemis TTS benchmark: load Kokoro locally and measure synthesis speed.

Usage:
    venv-tts/bin/python test_tts.py
    venv-tts/bin/python test_tts.py --voice bf_emma --out sample.wav
    venv-tts/bin/python test_tts.py --list-voices
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import soundfile as sf
from kokoro_onnx import Kokoro

from artemis.config import get_settings

DEFAULT_TEXT = (
    "Good evening. Artemis is online. All local systems are nominal, "
    "and speech is being generated entirely on this machine."
)


def main() -> int:
    settings = get_settings().tts

    parser = argparse.ArgumentParser(description="Artemis Kokoro TTS benchmark")
    parser.add_argument("--text", default=DEFAULT_TEXT)
    parser.add_argument("--voice", default=settings.voice)
    parser.add_argument("--speed", type=float, default=settings.speed)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--out", type=Path, help="Write the last result to a WAV file")
    parser.add_argument("--list-voices", action="store_true")
    args = parser.parse_args()

    print(f"Model:  {settings.model_path}")
    started = time.perf_counter()
    kokoro = Kokoro(str(settings.model_path), str(settings.voices_path))
    print(f"Load:   {time.perf_counter() - started:.2f}s")

    voices = sorted(kokoro.get_voices())
    if args.list_voices:
        print("\n".join(voices))
        return 0

    if args.voice not in voices:
        print(f"ERROR: unknown voice {args.voice!r}; use --list-voices")
        return 1

    print(f"Voice:  {args.voice}  speed {args.speed}")
    print("Warming up...")
    kokoro.create("Warm up.", voice=args.voice, speed=args.speed, lang=settings.lang)

    samples, sample_rate = None, 0
    timings: list[float] = []
    duration = 0.0

    for run in range(1, args.runs + 1):
        started = time.perf_counter()
        samples, sample_rate = kokoro.create(
            args.text, voice=args.voice, speed=args.speed, lang=settings.lang
        )
        elapsed = time.perf_counter() - started
        duration = len(samples) / sample_rate
        timings.append(elapsed)
        print(f"Run {run}: {duration:.2f}s audio in {elapsed:.2f}s (RTF {elapsed / duration:.3f})")

    best = min(timings)
    print("\n" + "=" * 50)
    print(f"Best: {best:.2f}s for {duration:.2f}s of audio")
    print(f"Real-time factor: {best / duration:.3f}x "
          f"({'faster' if best < duration else 'slower'} than real time)")
    print("=" * 50)

    if args.out and samples is not None:
        sf.write(args.out, samples, sample_rate)
        print(f"Wrote {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
