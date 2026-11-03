// Builds the two-sided transcript from streamed transcription pieces.
// Copied from Lucia: the user's transcription often arrives *after* Mario has
// started answering, so a user turn stays open until Mario's turn completes and
// late pieces are appended to it rather than starting a new bubble.

const ECHO_WINDOW_MS = 20000;
const ECHO_SCORE = 0.6;
const KEEP_SHORT = /^(evet|hayır|dur|tamam|iptal|vazgeç|olur|yes|no|stop|ok|okay)( \p{L}+)?$/u;

export class Transcript {
  constructor(onChange) {
    this.turns = [];
    this.pieces = []; // every user piece with its arrival time, for confirmations
    this.openUser = null;
    this.openAssistant = null;
    this.onChange = onChange;
  }

  addUser(piece, now = Date.now()) {
    if (!this.openUser) {
      this.openUser = { role: "user", text: "", at: now };
      // Place it before a reply that's already streaming.
      const i = this.openAssistant ? this.turns.indexOf(this.openAssistant) : this.turns.length;
      this.turns.splice(i, 0, this.openUser);
    }
    this.openUser.text += piece;
    this.pieces.push({ text: piece, at: now });
    this.onChange(this.openUser);
  }

  addAssistant(piece, now = Date.now()) {
    if (!this.openAssistant) {
      this.openAssistant = { role: "assistant", text: "", at: now };
      this.turns.push(this.openAssistant);
    }
    this.openAssistant.text += piece;
    this.onChange(this.openAssistant);
  }

  /** Adds a non-speech line: a tool call, a result, a system note. */
  addEvent(event) {
    const turn = { role: "event", at: Date.now(), ...event };
    this.turns.push(turn);
    this.onChange(turn);
    return turn;
  }

  /** Marks Mario's turn as cut off by the user (barge-in). */
  interrupt() {
    if (this.openAssistant) {
      this.openAssistant.interrupted = true;
      this.onChange(this.openAssistant);
    }
  }

  completeTurn(echoFilter = false, now = Date.now()) {
    const user = this.openUser;
    if (user) {
      user.text = user.text.trim();
      if (echoFilter && this.isEcho(user.text, now)) user.echo = true;
      this.onChange(user);
    }
    if (this.openAssistant) this.openAssistant.text = this.openAssistant.text.trim();
    this.openUser = this.openAssistant = null;
  }

  /** What the user said since `since` (ms), minus Mario's own voice picked up
   * by the mic. This is what the server checks a confirmation against. */
  userSince(since, now = Date.now()) {
    const text = this.pieces.filter((p) => p.at >= since).map((p) => p.text).join("").trim();
    if (!text || this.isEcho(text, now)) return [];
    return [text];
  }

  /** On a loudspeaker the mic can pick up Mario's own voice, which then shows
   * up as user speech. Same idea as the self-hearing filter in the Jarvis phone
   * page: if most words match what Mario said in the last 20 s, treat it as
   * echo. Short real replies like "evet" or "dur" are kept. */
  isEcho(text, now = Date.now()) {
    const heard = words(text);
    if (heard.length < 3 || KEEP_SHORT.test(heard.join(" "))) return false;
    const said = new Set(
      this.turns
        .filter((t) => t.role === "assistant" && now - t.at < ECHO_WINDOW_MS)
        .flatMap((t) => words(t.text)),
    );
    if (!said.size) return false;
    return heard.filter((w) => said.has(w)).length / heard.length >= ECHO_SCORE;
  }
}

function words(text) {
  return text.toLocaleLowerCase("tr").replace(/[^\p{L}\p{N}' ]+/gu, " ").split(/\s+/).filter(Boolean);
}
