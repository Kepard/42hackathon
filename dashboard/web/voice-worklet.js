/* Local microphone conversion. Audio is never written to disk or local storage. */
class HoloSpeedPCM extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.weight = 0;
    this.total = 0;
    this.offset = 0;
    this.frame = new ArrayBuffer(3200); // 100 ms of 16-bit mono audio.
    this.view = new DataView(this.frame);
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    for (const raw of channel) {
      let remaining = 1;
      const value = Number.isFinite(raw) ? Math.max(-1, Math.min(1, raw)) : 0;
      while (remaining > 0.0000001) {
        const portion = Math.min(remaining, this.ratio - this.weight);
        this.total += value * portion;
        this.weight += portion;
        remaining -= portion;
        if (this.weight + 0.0000001 >= this.ratio) {
          const averaged = this.total / this.ratio;
          this.view.setInt16(this.offset, Math.round(averaged * (averaged < 0 ? 32768 : 32767)), true);
          this.offset += 2;
          this.weight = 0;
          this.total = 0;
          if (this.offset === this.frame.byteLength) {
            this.port.postMessage(this.frame, [this.frame]);
            this.offset = 0;
            this.frame = new ArrayBuffer(3200);
            this.view = new DataView(this.frame);
          }
        }
      }
    }
    return true;
  }
}
registerProcessor("holospeed-pcm", HoloSpeedPCM);
