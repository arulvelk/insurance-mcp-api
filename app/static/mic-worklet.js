// Turns microphone audio into 24 kHz mono pcm16 chunks of about 100 ms for Voice Live.
const TARGET_RATE = 24000;
const CHUNK_SAMPLES = 2400;

class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.position = 0;
    this.chunk = new Int16Array(CHUNK_SAMPLES);
    this.filled = 0;
  }

  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    // Linear-interpolation resampler; `position` carries the fractional read offset across blocks.
    while (this.position < input.length - 1) {
      const i = Math.floor(this.position);
      const frac = this.position - i;
      const sample = input[i] + (input[i + 1] - input[i]) * frac;
      const clamped = Math.max(-1, Math.min(1, sample));
      this.chunk[this.filled++] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
      if (this.filled === CHUNK_SAMPLES) {
        this.port.postMessage(this.chunk.buffer, [this.chunk.buffer]);
        this.chunk = new Int16Array(CHUNK_SAMPLES);
        this.filled = 0;
      }
      this.position += this.ratio;
    }
    this.position -= input.length - 1;
    return true;
  }
}

registerProcessor("mic-processor", MicProcessor);
