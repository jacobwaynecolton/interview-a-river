// Captures mic audio, resamples to 24 kHz mono PCM16, posts ~20 ms chunks.
class MicProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 24000;
    this.pos = 0;
    this.out = new Int16Array(480);
    this.n = 0;
  }
  process(inputs) {
    const ch = inputs[0][0];
    if (!ch) return true;
    while (this.pos < ch.length) {
      const i = Math.floor(this.pos), f = this.pos - i;
      const s = ch[i] + ((ch[i + 1] ?? ch[i]) - ch[i]) * f;
      this.out[this.n++] = Math.max(-1, Math.min(1, s)) * 0x7fff;
      if (this.n === this.out.length) {
        this.port.postMessage(this.out.buffer.slice(0));
        this.n = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= ch.length;
    return true;
  }
}
registerProcessor("mic-processor", MicProcessor);
