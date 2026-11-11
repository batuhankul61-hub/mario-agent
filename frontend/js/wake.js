// "Mario" wake word while no voice session is open. Chrome's built-in speech
// recognition listens for the name; what follows it in the same sentence
// ("Mario, saat kaç?") becomes the first command of the session it opens.
// Cheaper than keeping a Live session open: Gemini only runs after the name.

const WAKE = /\bmar[iy]o\b[\s,.:!?-]*/i;

export class WakeWord {
  constructor(onWake) {
    this.onWake = onWake;
    this.active = false;
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    this.supported = Boolean(Rec);
    if (!Rec) return;
    this.rec = new Rec();
    this.rec.lang = "tr-TR";
    this.rec.continuous = true;
    this.rec.interimResults = true;
    this.rec.onresult = (e) => this.onResult(e);
    // Recognition stops by itself now and then; keep it going while wanted.
    this.rec.onend = () => this.active && setTimeout(() => this.listen(), 300);
    this.rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") this.active = false;
    };
  }

  start() {
    if (!this.supported || this.active) return;
    this.active = true;
    this.listen();
  }

  stop() {
    this.active = false;
    try { this.rec?.abort(); } catch {}
  }

  listen() {
    if (!this.active) return;
    try { this.rec.start(); } catch {} // already running
  }

  onResult(e) {
    for (let i = e.resultIndex; i < e.results.length; i++) {
      // Only whole sentences: an early "Mario" would cut "saat kaç" off.
      if (!e.results[i].isFinal) continue;
      const text = e.results[i][0].transcript;
      const m = text.match(WAKE);
      if (!m) continue;
      const rest = text.slice(m.index + m[0].length).trim();
      this.stop();
      this.onWake(rest);
      return;
    }
  }
}
