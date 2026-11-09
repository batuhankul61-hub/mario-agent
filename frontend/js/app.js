import { ApiError, accessCode, imageUrl } from "./api.js";
import { Agent } from "./session.js";
import { initDev } from "./dev.js";

const $ = (id) => document.getElementById(id);
const $$ = (sel) => document.querySelectorAll(sel);
const log = $("log");
const STATUS = {
  idle: "Hazır", connecting: "Bağlanıyor", listening: "Dinliyor", speaking: "Konuşuyor",
  working: "Çalışıyor", ended: "Hazır", error: "Bir sorun oluştu",
};
const TOOL_LABEL = {
  open_program: "Program aç", take_screenshot: "Ekran görüntüsü", lock_computer: "Ekranı kilitle",
  shutdown_computer: "Bilgisayarı kapat", cancel_shutdown: "Kapatmayı iptal et", get_weather: "Hava durumu",
  get_time: "Saat", delegate_to_claude: "Claude'a görev", check_claude_task: "Claude görev durumu",
  confirm_action: "Onay", cancel_action: "İptal", claude: "Claude görevi",
  get_pc_status: "PC durumu", set_volume: "Ses", set_microphone: "Mikrofon", set_audio_output: "Ses çıkışı",
  set_brightness: "Parlaklık", set_theme: "Tema", set_do_not_disturb: "Rahatsız etme", open_settings: "Ayarlar",
  set_radio: "Wi-Fi / Bluetooth", set_power_plan: "Güç planı", sleep_computer: "Uyku", list_programs: "Açık programlar",
  close_program: "Program kapat", list_emails: "Mail", read_email: "Mail oku", find_email_address: "Adres bul",
  send_email: "Mail gönder", reply_email: "Maili yanıtla", learn_skill: "Öğren", run_skill: "Öğrenilmiş yetenek",
  list_skills: "Yetenekler", forget_skill: "Yeteneği unut", learned: "Yeni yetenek",
};
// 24×24 stroke icons and the badge colour behind each, one per kind of tool.
const ICON = {
  pc: ["M3 5h18v11H3zM8 20h8m-4-4v4", "#5e5ce6"],
  camera: ["M4 8h3l2-3h6l2 3h3v11H4zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "#af52de"],
  sliders: ["M4 6h10m4 0h2M4 12h4m4 0h8M4 18h12m4 0h0M16 4v4M10 10v4M18 16v4", "#8e8e93"],
  mail: ["M3 6h18v12H3zm0 0 9 7 9-7", "#1e8cff"],
  cloud: ["M7 18h10a4 4 0 0 0 .5-7.97A6 6 0 0 0 6 11a3.5 3.5 0 0 0 1 7z", "#4d9bff"],
  clock: ["M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zm0-13v4l3 2", "#ff9500"],
  spark: ["M12 3v5m0 8v5M3 12h5m8 0h5M6 6l3 3m6 6 3 3m0-12-3 3m-6 6-3 3", "#d9774b"],
  book: ["M5 4h10a4 4 0 0 1 4 4v12H9a4 4 0 0 1-4-4zm0 12a4 4 0 0 1 4-4h10", "#34a853"],
  check: ["M5 12l5 5L20 7", "#34a853"],
};
const TOOL_ICON = {
  take_screenshot: "camera", get_weather: "cloud", get_time: "clock",
  delegate_to_claude: "spark", check_claude_task: "spark", claude: "spark",
  confirm_action: "check", cancel_action: "check", onay: "check",
};
for (const n of ["get_pc_status", "set_volume", "set_microphone", "set_audio_output", "set_brightness", "set_theme",
  "set_do_not_disturb", "open_settings", "set_radio", "set_power_plan"]) TOOL_ICON[n] = "sliders";
for (const n of ["list_emails", "read_email", "find_email_address", "send_email", "reply_email"]) TOOL_ICON[n] = "mail";
for (const n of ["learn_skill", "run_skill", "list_skills", "forget_skill", "learned"]) TOOL_ICON[n] = "book";
const ARG_LABEL = {
  name: "Ad", target: "Hedef", city: "Şehir", level: "Seviye", mute: "Sessiz", device: "Cihaz", mode: "Mod",
  on: "Açık", page: "Sayfa", plan: "Plan", radio: "Radyo", query: "Arama", max_results: "En fazla", to: "Alıcı",
  subject: "Konu", body: "Metin", task: "Görev", request: "İstek", change: "Değişiklik", args_json: "Parametreler",
};
const HIDDEN_ARGS = new Set(["confirmation_id", "id", "job_id"]);
// Success needs no label; everything else says what happened.
const STATUS_LABEL = {
  running: "Çalışıyor", error: "Hata", failed: "Başarısız", cancelled: "İptal edildi",
  needs_confirmation: "Onay bekliyor", not_confirmed: "Onaylanmadı", ambiguous: "Belirsiz",
};
const CLAMP_CHARS = 240;
const ARG_CHARS = 60;
const LAYOUTS = ["edge", "focus", "app"];
const THEMES = ["auto", "light", "dark"];
const prefs = {
  get: (k, d) => { try { return localStorage.getItem(`mario.${k}`) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(`mario.${k}`, v); } catch {} },
};

let agent = null;
let pendingId = null;
let micLevel = 0;
const rows = new WeakMap();

// ---------- Preferences: layout, colour mode, speaker ----------
function check(sel, attr, value) {
  for (const b of $$(sel)) b.setAttribute("aria-checked", String(b.dataset[attr] === value));
}
function setLayout(name) {
  if (!LAYOUTS.includes(name)) name = "edge";
  document.documentElement.dataset.layout = name;
  document.body.classList.remove("history", "typing");
  $("type-btn").setAttribute("aria-pressed", "false");
  check(".layouts [data-layout]", "layout", name);
  prefs.set("layout", name);
}
const darkQuery = matchMedia("(prefers-color-scheme: dark)");
function setTheme(name) {
  if (!THEMES.includes(name)) name = "auto";
  if (name === "auto") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = name;
  check(".seg [data-theme]", "theme", name);
  prefs.set("theme", name);
  syncThemeColor();
}
function syncThemeColor() {
  const dark = document.documentElement.dataset.theme === "dark" || (!document.documentElement.dataset.theme && darkQuery.matches);
  document.querySelector('meta[name="theme-color"]').content = dark ? "#000000" : "#f2f2f7";
}
darkQuery.addEventListener("change", syncThemeColor);
$("settings").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-layout], button[data-theme]");
  if (b?.dataset.layout) setLayout(b.dataset.layout);
  else if (b?.dataset.theme) setTheme(b.dataset.theme);
});
for (const b of $$("[data-open-settings]")) b.addEventListener("click", () => $("settings").showModal());
initDev(() => askCode());

function setSpeaker(on) {
  $("speaker").checked = on;
  $("speaker-btn").setAttribute("aria-pressed", String(on));
  prefs.set("speaker", on ? "1" : "0");
  // Takes effect at once: the session reads it for every mic chunk.
  if (agent) agent.speakerMode = on;
}
$("speaker").addEventListener("change", (e) => setSpeaker(e.target.checked));
$("speaker-btn").addEventListener("click", () => setSpeaker(!$("speaker").checked));

setLayout(prefs.get("layout", "edge"));
setTheme(prefs.get("theme", "auto"));
setSpeaker(prefs.get("speaker", matchMedia("(pointer: coarse)").matches ? "1" : "0") === "1");

// ---------- B · Odak: history sheet and typing ----------
$("history-btn").addEventListener("click", () => document.body.classList.toggle("history"));
$("history-close").addEventListener("click", () => document.body.classList.remove("history"));
$("type-btn").addEventListener("click", () => {
  const on = document.body.classList.toggle("typing");
  $("type-btn").setAttribute("aria-pressed", String(on));
  if (on) $("text").focus();
});

// ---------- State ----------
function setState(state) {
  document.body.dataset.state = state;
  const muted = state === "listening" && document.body.classList.contains("mic-off");
  for (const n of $$("[data-status]")) n.textContent = muted ? "Mikrofon kapalı" : STATUS[state] ?? state;
  if (state === "idle" || state === "ended") {
    $("cap").textContent = "Konuşmak için başlat.";
    $("cap-prev").textContent = "";
  }
}

// The orb (and in A the screen's edge) follows whoever is talking.
let amp = 0;
function frame() {
  const state = document.body.dataset.state;
  const raw = state === "speaking" ? (agent?.audio.outputLevel() ?? 0) * 5 : state === "listening" ? micLevel * 6 : 0;
  const target = Math.min(1, raw);
  amp += (target - amp) * (target > amp ? 0.5 : 0.12);
  document.body.style.setProperty("--amp", amp.toFixed(3));
  if (agent || amp > 0.01) requestAnimationFrame(frame);
  else document.body.style.setProperty("--amp", "0");
}

const pad = (n) => String(n).padStart(2, "0");
const mmss = (ms) => `${pad(Math.floor(ms / 60000))}:${pad(Math.floor(ms / 1000) % 60)}`;
function tick() {
  const a = agent;
  for (const n of $$("[data-session]")) n.textContent = a?.startedAt ? mmss(Date.now() - a.startedAt) : "—";
  const jobs = a?.jobTimers.size ?? 0;
  for (const n of $$("[data-jobs]")) n.textContent = jobs ? `Arka planda ${jobs} görev` : "Arka planda görev yok";
}
setInterval(tick, 1000);
tick();

// ---------- Conversation ----------
function scrollDown() {
  log.scrollTop = log.scrollHeight;
  log.parentElement.scrollTop = log.parentElement.scrollHeight;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
}

function svgIcon(path) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", "i");
  svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS("http://www.w3.org/2000/svg", "path");
  p.setAttribute("d", path);
  svg.append(p);
  return svg;
}

function line(cls, text) {
  const row = el("div", `msg ${cls}`, text);
  log.append(row);
  scrollDown();
  return row;
}

function renderTurn(turn) {
  let row = rows.get(turn);
  if (!row) {
    row = document.createElement("div");
    rows.set(turn, row);
    // A user turn can be created after Mario's reply began; keep the order.
    const i = agent.transcript.turns.indexOf(turn);
    const next = agent.transcript.turns.slice(i + 1).map((t) => rows.get(t)).find(Boolean);
    next ? log.insertBefore(row, next) : log.append(row);
  }
  if (turn.role === "event") renderEvent(row, turn);
  else {
    row.className = `msg ${turn.role}${turn.echo ? " echo" : ""}${turn.interrupted ? " cut" : ""}`;
    row.textContent = turn.text;
  }
  updateCaption(turn);
  scrollDown();
}

/** B · Odak shows only the last words: what you said, small, and Mario's reply, large. */
function updateCaption(turn) {
  if (turn.role === "user" && !turn.echo && turn.text.trim()) {
    $("cap-prev").textContent = `“${turn.text.trim()}”`;
    $("cap").textContent = "";
  } else if (turn.role === "assistant") {
    $("cap").textContent = turn.text;
  } else if (turn.role === "event" && turn.status === "running") {
    $("activity-text").textContent = `${TOOL_LABEL[turn.name] ?? turn.name} çalışıyor`;
  }
}

/** Tool arguments as "Label: value". run_skill passes its own arguments as a JSON
 * string, which is unpacked so they read like any other tool's. */
function argText(args = {}) {
  let entries = Object.entries(args);
  if (typeof args.args_json === "string") {
    try {
      const inner = JSON.parse(args.args_json);
      if (inner && typeof inner === "object") entries = [...entries.filter(([k]) => k !== "args_json"), ...Object.entries(inner)];
    } catch {}
  }
  return entries
    .filter(([k, v]) => !HIDDEN_ARGS.has(k) && v !== "" && v != null)
    .map(([k, v]) => {
      const s = typeof v === "boolean" ? (v ? "evet" : "hayır") : String(v);
      return `${ARG_LABEL[k] ?? k}: ${s.length > ARG_CHARS ? `${s.slice(0, ARG_CHARS)}…` : s}`;
    })
    .join(" · ");
}

function renderEvent(card, turn) {
  const r = turn.result ?? {};
  card.className = `msg tool ${turn.status}`;
  card.replaceChildren();

  const [path, color] = ICON[TOOL_ICON[turn.name] ?? "pc"];
  const badge = el("span", "badge");
  badge.style.background = color;
  badge.append(svgIcon(path));
  const head = el("div", "tool-head");
  head.append(badge, el("b", null, `${TOOL_LABEL[turn.name] ?? turn.name}${turn.byButton ? " · butonla" : ""}`));
  const label = STATUS_LABEL[turn.status];
  if (label) {
    const st = el("span", "st");
    if (turn.status === "running") st.append(el("span", "spinner"));
    st.append(label);
    head.append(st);
  }
  card.append(head);

  const args = argText(turn.args);
  if (args) card.append(el("p", "tool-args", args));

  const text = turn.status === "running" ? "" : String(r.message ?? r.result ?? "");
  if (text) {
    const body = el("p", "tool-body", text);
    card.append(body);
    if (text.length > CLAMP_CHARS) {
      body.classList.add("clamped");
      const more = el("button", "more", "Devamını göster");
      more.type = "button";
      more.addEventListener("click", () => {
        more.textContent = body.classList.toggle("clamped") ? "Devamını göster" : "Daha az göster";
      });
      card.append(more);
    }
  }

  if (r.image_url) {
    const shot = el("button", "shot");
    shot.type = "button";
    shot.setAttribute("aria-label", "Ekran görüntüsünü büyüt");
    card.append(shot);
    // Cards are re-rendered on every update; fetch the image only once.
    (turn.imageSrc ??= imageUrl(r.image_url))
      .then((url) => {
        const img = el("img");
        img.src = url;
        img.alt = "Ekran görüntüsü";
        img.onload = scrollDown;
        shot.append(img);
        shot.addEventListener("click", () => openViewer(url));
      })
      .catch(() => shot.remove());
  }
}

function openViewer(url) {
  const viewer = $("viewer");
  const img = viewer.querySelector("img") ?? viewer.insertBefore(el("img"), viewer.querySelector("a"));
  img.alt = "Ekran görüntüsü";
  img.src = url;
  viewer.querySelector("a").href = url;
  viewer.showModal();
}
// A tap on the dark backdrop closes the viewer.
$("viewer").addEventListener("click", (e) => e.target === e.currentTarget && e.currentTarget.close());

// ---------- Confirmation ----------
function showConfirm({ id, question, tool }) {
  pendingId = id;
  $("confirm-title").textContent = TOOL_LABEL[tool] ? `${TOOL_LABEL[tool]}?` : "Onay gerekiyor";
  $("confirm-q").textContent = question;
  $("confirm").hidden = false;
  // The safe choice gets focus, so a stray Enter never approves anything.
  $("confirm-no").focus();
}

function hideConfirm(id) {
  if (id && id !== pendingId) return;
  pendingId = null;
  $("confirm").hidden = true;
}

async function onConfirmButton(approved) {
  const id = pendingId;
  hideConfirm();
  if (!id || !agent) return;
  try {
    await agent.answerByButton(id, approved);
  } catch (err) {
    line("error", err.message);
  }
}
$("confirm-yes").addEventListener("click", () => onConfirmButton(true));
$("confirm-no").addEventListener("click", () => onConfirmButton(false));
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("confirm").hidden) onConfirmButton(false);
  else document.body.classList.remove("history");
});

function askCode() {
  const dialog = $("code-dialog");
  dialog.querySelector("input").value = accessCode.get();
  dialog.showModal();
  return new Promise((resolve) =>
    dialog.addEventListener("close", () => {
      if (dialog.returnValue === "ok") accessCode.set(dialog.querySelector("input").value.trim());
      resolve(dialog.returnValue === "ok");
    }, { once: true }),
  );
}

// ---------- Session ----------
async function start() {
  log.querySelector(".intro")?.remove();
  agent = new Agent({ speakerMode: $("speaker").checked });
  agent.addEventListener("state", (e) => setState(e.detail));
  agent.addEventListener("turn", (e) => renderTurn(e.detail));
  agent.addEventListener("confirm", (e) => showConfirm(e.detail));
  agent.addEventListener("confirmDone", (e) => hideConfirm(e.detail));
  agent.addEventListener("level", (e) => (micLevel = e.detail));
  agent.addEventListener("notice", (e) => line("sys", e.detail));
  agent.addEventListener("error", (e) => line("error", `Hata: ${e.detail}`));
  agent.addEventListener("dropped", () => { line("sys", "Bağlantı kapandı."); stop(); });
  agent.addEventListener("timeup", () => { line("sys", "Oturum süresi doldu."); stop(); });
  setMain(true);
  requestAnimationFrame(frame);
  try {
    await agent.start();
  } catch (err) {
    await stop();
    if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
      line("error", err.message);
      if (await askCode()) start();
      return;
    }
    setState("error");
    line("error", err.name === "NotAllowedError" ? "Mikrofon izni verilmedi." : err.message);
  }
}

async function stop() {
  const a = agent;
  agent = null;
  hideConfirm();
  setMain(false);
  await a?.end();
  setState("idle");
}

/** Closes or opens the mic for the running session; Mario keeps talking either way. */
function setMuted(on) {
  if (agent) agent.muted = on;
  document.body.classList.toggle("mic-off", on);
  for (const b of $$("[data-mute]")) {
    b.setAttribute("aria-pressed", String(on));
    b.setAttribute("aria-label", on ? "Mikrofonu aç" : "Mikrofonu kapat");
    b.title = on ? "Mikrofonu aç (M)" : "Mikrofonu kapat (M)";
  }
  for (const n of $$(".mute-label")) n.textContent = on ? "Sesi aç" : "Sessiz";
  if (on) micLevel = 0;
  setState(document.body.dataset.state);
}
for (const b of $$("[data-mute]")) b.addEventListener("click", () => agent && setMuted(!agent.muted));
// M toggles the mic, unless the user is typing somewhere.
document.addEventListener("keydown", (e) => {
  if (e.key.toLowerCase() !== "m" || e.ctrlKey || e.metaKey || e.altKey || !agent) return;
  if (e.target.closest?.("input, textarea, [contenteditable]") || document.querySelector("dialog[open]")) return;
  e.preventDefault();
  setMuted(!agent.muted);
});

function setMain(running) {
  if (!running) setMuted(false);
  document.body.classList.toggle("live", running);
  for (const b of $$("[data-toggle]")) {
    b.setAttribute("aria-label", running ? "Bitir" : "Başlat");
    b.setAttribute("aria-pressed", String(running));
  }
  for (const n of $$(".go-label")) n.textContent = running ? "Bitir" : "Başlat";
  if (!running) micLevel = 0;
}

/** Typed words or a suggestion: starts a session first if none is running. */
async function say(text) {
  text = text.trim();
  if (!text) return;
  if (!agent) await start();
  agent?.say(text);
}

for (const b of $$("[data-toggle]")) b.addEventListener("click", () => (agent ? stop() : start()));
for (const b of $$("[data-say]")) b.addEventListener("click", () => say(b.dataset.say));
$("text-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("text").value;
  $("text").value = "";
  say(text);
});

// Phones opened from a link like /#code=XYZ remember the code.
const fromLink = location.hash.match(/code=([^&]+)/);
if (fromLink) {
  accessCode.set(decodeURIComponent(fromLink[1]));
  history.replaceState(null, "", location.pathname);
}
