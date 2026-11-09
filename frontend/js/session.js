// One voice session with Mario: wires the microphone, the Live session, the
// speaker and the transcript together (adapted from Lucia's session.js), and
// relays every function call to the backend, which actually runs it.

import { answerConfirmation, createToken, jobStatus, runTool } from "./api.js";
import { AudioIO } from "./audio/audio-io.js";
import { LiveSession } from "./live.js";
import { Transcript } from "./transcript.js";

// In speaker mode, mic audio is only sent while Mario talks if it's clearly
// louder than the echo, so Mario doesn't interrupt itself but the user can
// still cut in by speaking up.
const SPEAKER_GATE_RMS = 0.08;
const ECHO_TAIL_MS = 400;
// The user's transcription can trail the model's function call a little.
const TRANSCRIPT_WAIT_MS = 2500;
const JOB_POLL_MS = 5000;
const KICKOFF = "(Sistem notu: Kullanıcı bağlandı. Tek cümleyle selam ver.)";

export class Agent extends EventTarget {
  constructor({ speakerMode = false } = {}) {
    super();
    this.speakerMode = speakerMode;
    this.audio = new AudioIO();
    this.transcript = new Transcript((turn) => this.emit("turn", turn));
    this.state = "idle";
    this.playing = false;
    this.drainedAt = 0;
    this.ended = false;
    this.muted = false;
    // When the last confirmation question was asked; only words said after
    // this count as the user's answer.
    this.confirmMark = 0;
    this.jobTimers = new Map();
  }

  emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }

  setState(state) {
    this.state = state;
    this.emit("state", state);
  }

  get speaking() {
    return this.playing || performance.now() - this.drainedAt < ECHO_TAIL_MS;
  }

  /** Call from a tap handler: iOS only allows audio to start inside a user gesture. */
  async start() {
    this.setState("connecting");
    this.audio.onChunk = (c) => this.onMic(c);
    this.audio.onPlayback = (e) => this.onPlayback(e);
    try {
      await this.audio.start();
    } catch (err) {
      if (err.name !== "NotAllowedError" && err.name !== "NotFoundError") throw err;
      // No microphone (denied, or none attached): Mario still talks and takes typed commands.
      await this.audio.stop();
      await this.audio.start({ mic: false });
      this.emit("notice", "Mikrofon yok ya da izin verilmedi; komutları yazarak verebilirsin.");
    }

    const { token, model, max_session_minutes } = await createToken();
    this.live = new LiveSession({ token, model });
    this.live.addEventListener("audio", (e) => this.audio.play(e.detail));
    this.live.addEventListener("userText", (e) => this.transcript.addUser(e.detail));
    this.live.addEventListener("assistantText", (e) => this.transcript.addAssistant(e.detail));
    this.live.addEventListener("interrupted", () => this.onInterrupted());
    this.live.addEventListener("turnComplete", () => this.transcript.completeTurn(this.speakerMode));
    this.live.addEventListener("toolCall", (e) => this.onToolCall(e.detail));
    this.live.addEventListener("toolCancel", (e) => this.emit("notice", `İptal edilen araç çağrısı: ${e.detail.join(", ")}`));
    this.live.addEventListener("goAway", () => this.emit("notice", "Ses oturumu birazdan zaman aşımına uğrayacak."));
    this.live.addEventListener("error", (e) => !this.ended && this.emit("error", e.detail));
    this.live.addEventListener("close", () => !this.ended && this.emit("dropped"));

    await this.live.connect();
    this.setState("listening");
    this.live.sendText(KICKOFF);
    this.startedAt = Date.now();
    this.limitMs = max_session_minutes * 60_000;
    this.timer = setTimeout(() => this.emit("timeup"), this.limitMs);
  }

  onMic({ pcm, rms }) {
    // Muted: keep streaming, but silence, so the server's voice detection
    // still ends the user's turn and Mario can't be cut off by room noise.
    if (this.muted) {
      pcm.fill(0);
      rms = 0;
    }
    this.emit("level", rms);
    if (!this.live?.ready) return;
    if (this.speakerMode && this.speaking && rms < SPEAKER_GATE_RMS) pcm.fill(0);
    this.live.sendAudio(pcm);
  }

  onPlayback({ type }) {
    this.playing = type === "started";
    if (type === "drained") this.drainedAt = performance.now();
    if (!this.ended && this.live?.ready) this.setState(this.playing ? "speaking" : "listening");
  }

  onInterrupted() {
    this.audio.clearPlayback();
    this.transcript.interrupt();
  }

  /** Typed input counts as the user's words, including for confirmations. */
  say(text) {
    text = text.trim();
    if (!text || !this.live?.ready) return;
    this.transcript.addUser(text);
    this.transcript.completeTurn();
    this.live.sendText(text);
  }

  async onToolCall(calls) {
    this.setState("working");
    const responses = await Promise.all(calls.map((fc) => this.runCall(fc)));
    this.live?.sendToolResponse(responses);
  }

  async runCall({ id, name, args = {} }) {
    const event = this.transcript.addEvent({ kind: "tool", name, args, status: "running" });
    let result;
    try {
      const utterances = name === "confirm_action" ? await this.answerSinceQuestion() : [];
      result = await runTool(name, args, utterances);
    } catch (err) {
      result = { status: "error", message: err.message };
    }
    Object.assign(event, { status: result.status, result });
    this.emit("turn", event);

    if (result.status === "needs_confirmation") {
      this.confirmMark = Date.now();
      this.emit("confirm", { id: result.confirmation_id, question: result.question, tool: name });
    }
    const answered = name === "cancel_action" || (name === "confirm_action" && result.status !== "not_confirmed");
    if (answered) this.emit("confirmDone", args.confirmation_id);
    if (result.job_id) this.watchJob(result.job_id);
    return { id, name, response: result };
  }

  async answerSinceQuestion() {
    const deadline = Date.now() + TRANSCRIPT_WAIT_MS;
    while (Date.now() < deadline && !this.transcript.userSince(this.confirmMark).length) await sleep(100);
    await sleep(300); // let the last pieces of the sentence land
    return this.transcript.userSince(this.confirmMark);
  }

  /** The Onayla / İptal buttons: the user answers directly, no model involved. */
  async answerByButton(id, approved) {
    const result = await answerConfirmation(id, approved);
    this.transcript.addEvent({ kind: "tool", name: result.tool ?? "onay", args: {}, status: result.status, result, byButton: true });
    if (result.job_id) this.watchJob(result.job_id);
    this.live?.sendText(
      `(Sistem notu: Kullanıcı ekrandaki butonla ${approved ? "onayladı" : "iptal etti"}. Sonuç: ${result.message} ` +
        `Bu iş için confirm_action ya da cancel_action çağırma.)`,
    );
    return result;
  }

  watchJob(jobId) {
    const poll = async () => {
      if (this.ended) return;
      try {
        const job = await jobStatus(jobId);
        if (job.state !== "running") {
          this.jobTimers.delete(jobId);
          this.transcript.addEvent({ kind: "job", name: job.kind === "skill" ? "learned" : "claude", status: job.state, result: job });
          this.live?.sendText(jobNote(job));
          return;
        }
      } catch {}
      this.jobTimers.set(jobId, setTimeout(poll, JOB_POLL_MS));
    };
    this.jobTimers.set(jobId, setTimeout(poll, JOB_POLL_MS));
  }

  async end() {
    if (this.ended) return;
    this.ended = true;
    clearTimeout(this.timer);
    this.jobTimers.forEach((t) => clearTimeout(t));
    this.transcript.completeTurn(this.speakerMode);
    this.live?.close();
    await this.audio.stop();
    this.setState("ended");
  }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** What Mario is told when a Claude job ends. A learned skill comes with
 * everything needed to call it right away, because the tool list of this
 * session was locked before the skill existed. */
function jobNote(job) {
  if (job.kind !== "skill") {
    return (
      `(Sistem notu: Claude'a verdiğin görev ${job.state === "done" ? "bitti" : "başarısız oldu"}. ` +
      `Görev: ${job.task}. Sonuç: ${job.result} Bunu kullanıcıya kısaca söyle; araç çağırma.)`
    );
  }
  if (job.state !== "done" || !job.skill) {
    return (
      `(Sistem notu: Claude şu isteği sana öğretemedi: ${job.task}. Sonuç: ${job.result} ` +
      `Bunu kullanıcıya kısaca söyle; araç çağırma.)`
    );
  }
  const s = job.skill;
  const params = Object.entries(s.parameters).map(([k, v]) => `${k} (${v})`).join(", ") || "yok";
  return (
    `(Sistem notu: Claude sana yeni bir yetenek öğretti: ${s.name} — ${s.description} Parametreler: ${params}. ` +
    `${job.result} Kullanıcıya öğrendiğini tek cümleyle söyle, sonra ilk isteğini ("${job.task}") hemen ` +
    `run_skill(name="${s.name}", args_json=...) ile yap.` +
    (s.confirm ? " Yetenek onay soracak; soruyu sor ve cevabı bekle.)" : ")")
  );
}
