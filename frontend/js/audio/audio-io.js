// Copied from Lucia. Microphone in, Mario's voice out, sharing one AudioContext (iOS Safari
// behaves best with a single context created inside a user gesture).

export class AudioIO {
  constructor() {
    this.ctx = null;
    this.stream = null;
    this.mic = null;
    this.player = null;
    this.onChunk = () => {};
    this.onPlayback = () => {};
  }

  /** Must be called from a click/tap handler. With `mic: false` only the
   * speaker is set up (Mario then takes typed commands). */
  async start({ mic = true } = {}) {
    this.ctx = new AudioContext({ latencyHint: "interactive" });
    if (this.ctx.state === "suspended") await this.ctx.resume();

    await Promise.all([
      this.ctx.audioWorklet.addModule(new URL("./mic-worklet.js", import.meta.url)),
      this.ctx.audioWorklet.addModule(new URL("./player-worklet.js", import.meta.url)),
    ]);

    this.player = new AudioWorkletNode(this.ctx, "player-processor", { outputChannelCount: [1] });
    this.player.port.onmessage = (e) => this.onPlayback(e.data);
    this.player.connect(this.ctx.destination);

    if (!mic) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.mic = new AudioWorkletNode(this.ctx, "mic-processor", { numberOfInputs: 1, numberOfOutputs: 0 });
    this.mic.port.onmessage = (e) => this.onChunk(e.data);
    source.connect(this.mic);
  }

  play(int16) {
    this.player?.port.postMessage({ type: "push", samples: int16 }, [int16.buffer]);
  }

  clearPlayback() {
    this.player?.port.postMessage({ type: "clear" });
  }

  async stop() {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.mic?.disconnect();
    this.player?.disconnect();
    if (this.ctx && this.ctx.state !== "closed") await this.ctx.close();
    this.ctx = this.stream = this.mic = this.player = null;
  }
}
