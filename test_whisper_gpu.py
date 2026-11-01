
#!/usr/bin/env python3

import argparse
import time
import sys
from pathlib import Path

import librosa
import numpy as np
import openvino_genai as ov_genai


# ==========================================
# ARTEMIS WHISPER GPU BENCHMARK
# Intel Arc Pro B50 - OpenVINO
# ==========================================

BASE_DIR = Path(__file__).resolve().parent

MODEL_PATH = (
    BASE_DIR
    / "models"
    / "whisper-large-v3-turbo-ready"
)

GPU_DEVICE = "GPU.0"
SAMPLE_RATE = 16000


def load_audio(audio_path):
    print("\n[1/4] Loading audio...")

    start = time.perf_counter()

    audio, sr = librosa.load(
        str(audio_path),
        sr=SAMPLE_RATE,
        mono=True
    )

    audio = np.asarray(audio, dtype=np.float32)

    duration = len(audio) / SAMPLE_RATE

    print(f"File: {audio_path.name}")
    print(f"Sample rate: {sr} Hz")
    print(f"Duration: {duration:.2f} seconds")
    print(f"Load time: {time.perf_counter() - start:.2f}s")

    if len(audio) == 0:
        raise ValueError("Audio file is empty")

    return audio, duration


def load_model():
    print("\n[2/4] Loading Whisper model...")
    print(f"Model: {MODEL_PATH.name}")
    print(f"Device: {GPU_DEVICE}")

    start = time.perf_counter()

    pipeline = ov_genai.WhisperPipeline(
        str(MODEL_PATH),
        GPU_DEVICE
    )

    elapsed = time.perf_counter() - start

    print(f"Model load time: {elapsed:.2f}s")

    return pipeline, elapsed


def run_benchmark(pipeline, audio, duration, language):
    print("\n[3/4] Running GPU transcription...")

    language_token = (
        "<|en|>" if language == "en"
        else "<|es|>" if language == "es"
        else None
    )

    # Warmup inference
    print("Warming up GPU...")

    pipeline.generate(
        [0.0] * SAMPLE_RATE,
        max_new_tokens=32,
        language="<|en|>",
        task="transcribe"
    )

    print("Starting benchmark...")

    start = time.perf_counter()

    options = {
        "max_new_tokens": 448,
        "task": "transcribe"
    }

    if language_token:
        options["language"] = language_token

    result = pipeline.generate(
        audio.tolist(),
        **options
    )

    inference_time = time.perf_counter() - start

    transcription = (
        result.texts[0]
        if hasattr(result, "texts")
        else str(result)
    )

    rtf = inference_time / duration

    print("\n[4/4] Benchmark complete!")

    return transcription, inference_time, rtf


def main():
    parser = argparse.ArgumentParser(
        description="Artemis Whisper GPU benchmark"
    )

    parser.add_argument(
        "audio_file",
        help="Path to MP3, WAV, or supported audio file"
    )

    parser.add_argument(
        "--language",
        choices=["en", "es", "auto"],
        default="en",
        help="Speech language (default: en)"
    )

    args = parser.parse_args()

    audio_path = Path(args.audio_file).expanduser().resolve()

    if not audio_path.is_file():
        print(f"ERROR: File not found: {audio_path}")
        sys.exit(1)

    if not MODEL_PATH.is_dir():
        print(f"ERROR: Model not found: {MODEL_PATH}")
        sys.exit(1)

    try:
        audio, duration = load_audio(audio_path)

        pipeline, load_time = load_model()

        transcription, inference_time, rtf = run_benchmark(
            pipeline,
            audio,
            duration,
            args.language
        )

        print("\n" + "=" * 55)
        print("ARTEMIS WHISPER GPU BENCHMARK RESULTS")
        print("=" * 55)

        print("\nTRANSCRIPTION:")
        print(transcription)

        print("\nPERFORMANCE:")
        print(f"GPU: Intel Arc Pro B50 ({GPU_DEVICE})")
        print("Model: Whisper Large-v3-Turbo")
        print(f"Model load time: {load_time:.2f}s")
        print(f"Audio duration: {duration:.2f}s")
        print(f"Inference time: {inference_time:.2f}s")
        print(f"Real-time factor: {rtf:.3f}x")

        if rtf < 1:
            print("Result: Faster than real-time")
        else:
            print("Result: Slower than real-time")

        print("=" * 55)

    except Exception as error:
        print(f"\nBENCHMARK FAILED: {error}")
        raise


if __name__ == "__main__":
    main()
