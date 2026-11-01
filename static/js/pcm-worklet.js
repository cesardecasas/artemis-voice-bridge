// @ts-nocheck: AudioWorkletGlobalScope (AudioWorkletProcessor, registerProcessor) is not in the DOM lib.
// AudioWorklet: microphone → 16 kHz mono PCM16 frames for hands-free mode.
// Runs on the audio thread, so it is plain JS (no modules, no DOM types).
// Posts one ArrayBuffer of Int16 samples every ~100 ms.

const TARGET_RATE = 16000;
const FRAME_SAMPLES = 1600; // 100 ms at 16 kHz

class PcmDownsampler extends AudioWorkletProcessor {
  constructor() {
    super();
    // `sampleRate` is the AudioContext rate (a global in the worklet scope).
    this.step = sampleRate / TARGET_RATE;
    this.position = 0; // fractional read position into the current input block
    this.previous = 0; // last sample of the previous block, for interpolation
    this.frame = new Int16Array(FRAME_SAMPLES);
    this.filled = 0;
  }

  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) {
      return true;
    }

    // Linear interpolation; the browser's mic path already band-limits voice.
    while (this.position < channel.length) {
      const index = Math.floor(this.position);
      const fraction = this.position - index;
      const a = index === 0 ? this.previous : channel[index - 1];
      const b = channel[index];
      const sample = Math.max(-1, Math.min(1, a + (b - a) * fraction));
      this.frame[this.filled++] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;

      if (this.filled === FRAME_SAMPLES) {
        const out = this.frame.slice().buffer;
        this.port.postMessage(out, [out]);
        this.filled = 0;
      }
      this.position += this.step;
    }

    this.position -= channel.length;
    this.previous = channel[channel.length - 1];
    return true;
  }
}

registerProcessor("pcm-downsampler", PcmDownsampler);
