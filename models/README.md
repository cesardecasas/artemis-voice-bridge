# `models/`: local model files

Everything Artemis needs to run offline. **The model files are git-ignored** (about 2 GB); only this README is tracked. Download instructions and checksums are in the main README under **Setup → Models**.

| Folder | Model | Size | Used by |
|---|---|---|---|
| `whisper-large-v3-turbo-ready/` | Whisper Large-v3-Turbo, OpenVINO IR (encoder, decoder, tokenizer) | 1.6 GB | STT service (`ARTEMIS_STT_MODEL_DIR`), on the Arc B50 |
| `kokoro/` | Kokoro-82M v1.0 ONNX + voice pack | 338 MB | TTS service (`ARTEMIS_TTS_MODEL`, `ARTEMIS_TTS_VOICES`) |
| `silero/` | Silero VAD v5.1.2 ONNX | 2.3 MB | Voice app, hands-free mode (`ARTEMIS_VAD_MODEL`). Optional: without it the hands-free button is hidden |
| `whisper-large-v3-turbo/` | An earlier copy of the Whisper export (no `added_tokens.json`, `merges.txt` or `normalizer.json`) | 1.6 GB | **Nothing.** Safe to delete to free 1.6 GB |

The LLM is not here: Hermes runs Gemma 4 on its own vLLM server (`127.0.0.1:8003`).

Back these files up or re-download them; they never change while Artemis runs.
