import { ApiError, accessCode, imageUrl } from "./api.js";
import { Agent } from "./session.js";

const $ = (id) => document.getElementById(id);
const log = $("log");
const STATUS = {
  idle: "HAZIR", connecting: "BAĞLANIYOR", listening: "DİNLİYOR", speaking: "KONUŞUYOR",
  working: "ÇALIŞIYOR", ended: "BİTTİ", error: "HATA",
};
const TOOL_LABEL = {
  open_program: "Program aç", take_screenshot: "Ekran görüntüsü", lock_computer: "Ekranı kilitle",
  shutdown_computer: "Bilgisayarı kapat", cancel_shutdown: "Kapatmayı iptal et", get_weather: "Hava durumu",
  get_time: "Saat", delegate_to_claude: "Claude'a görev", check_claude_task: "Claude görev durumu",
  confirm_action: "Onay", cancel_action: "İptal", claude: "Claude görevi",
  get_pc_status: "PC durumu", set_volume: "Ses", set_audio_output: "Ses çıkışı", set_brightness: "Parlaklık",
  set_theme: "Tema", set_do_not_disturb: "Rahatsız etme", open_settings: "Ayarlar", set_radio: "Wi-Fi / Bluetooth",
  set_power_plan: "Güç planı", sleep_computer: "Uyku", list_programs: "Açık programlar", close_program: "Program kapat",
  list_emails: "E-postalar", read_email: "E-posta oku", find_email_address: "Adres bul", send_email: "E-posta gönder",
  reply_email: "E-postayı yanıtla", learn_skill: "Öğren", run_skill: "Öğrenilmiş yetenek", list_skills: "Yetenekler",
  forget_skill: "Yeteneği unut", learned: "Yeni yetenek",
};
const prefs = {
  get: (k, d) => { try { return localStorage.getItem(`mario.${k}`) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem(`mario.${k}`, v); } catch {} },
};

let agent = null;
let pendingId = null;
const rows = new WeakMap();

$("speaker").checked = prefs.get("speaker", matchMedia("(pointer: coarse)").matches ? "1" : "0") === "1";
$("speaker").addEventListener("change", (e) => prefs.set("speaker", e.target.checked ? "1" : "0"));

function setState(state) {
  document.body.dataset.state = state;
  $("status-text").textContent = STATUS[state] ?? state.toUpperCase();
}

function scrollDown() {
  log.scrollTop = log.scrollHeight;
}

function line(cls, text) {
  const el = document.createElement("div");
  el.className = `msg ${cls}`;
  el.textContent = text;
  log.append(el);
  scrollDown();
  return el;
}

function renderTurn(turn) {
  let el = rows.get(turn);
  if (!el) {
    el = document.createElement("div");
    rows.set(turn, el);
    // A user turn can be created after Mario's reply began; keep the order.
    const i = agent.transcript.turns.indexOf(turn);
    const next = agent.transcript.turns.slice(i + 1).map((t) => rows.get(t)).find(Boolean);
    next ? log.insertBefore(el, next) : log.append(el);
  }
  if (turn.role === "event") renderEvent(el, turn);
  else {
    el.className = `msg ${turn.role}${turn.echo ? " echo" : ""}${turn.interrupted ? " cut" : ""}`;
    el.textContent = turn.text;
  }
  scrollDown();
}

function renderEvent(el, turn) {
  const r = turn.result ?? {};
  el.className = `msg tool ${turn.status}`;
  const args = Object.values(turn.args ?? {}).filter(Boolean).join(", ");
  el.innerHTML = "";
  const head = document.createElement("b");
  head.textContent = `${TOOL_LABEL[turn.name] ?? turn.name}${args ? ` · ${args}` : ""}${turn.byButton ? " · buton" : ""}`;
  const body = document.createElement("span");
  body.textContent = turn.status === "running" ? "çalışıyor…" : (r.message ?? r.result ?? turn.status);
  el.append(head, body);
  if (r.image_url && !el.dataset.img) {
    el.dataset.img = "1";
    imageUrl(r.image_url)
      .then((url) => {
        const img = document.createElement("img");
        img.src = url;
        img.alt = "Ekran görüntüsü";
        img.addEventListener("click", () => window.open(url, "_blank"));
        el.append(img);
        img.onload = scrollDown;
      })
      .catch(() => {});
  }
}

function showConfirm({ id, question }) {
  pendingId = id;
  $("confirm-q").textContent = question;
  $("confirm").hidden = false;
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

async function start() {
  log.querySelector(".intro")?.remove();
  agent = new Agent({ speakerMode: $("speaker").checked });
  agent.addEventListener("state", (e) => setState(e.detail));
  agent.addEventListener("turn", (e) => renderTurn(e.detail));
  agent.addEventListener("confirm", (e) => showConfirm(e.detail));
  agent.addEventListener("confirmDone", (e) => hideConfirm(e.detail));
  agent.addEventListener("level", (e) => ($("level").style.width = `${Math.min(100, e.detail * 400)}%`));
  agent.addEventListener("notice", (e) => line("sys", e.detail));
  agent.addEventListener("error", (e) => line("error", `Hata: ${e.detail}`));
  agent.addEventListener("dropped", () => { line("sys", "Bağlantı kapandı."); stop(); });
  agent.addEventListener("timeup", () => { line("sys", "Oturum süresi doldu."); stop(); });
  setMain(true);
  try {
    await agent.start();
    setInput(true);
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
  setInput(false);
  await a?.end();
  setState("idle");
}

function setMain(running) {
  $("main").textContent = running ? "Bitir" : "Başlat";
  $("main").classList.toggle("danger", running);
  $("main").classList.toggle("primary", !running);
}

function setInput(on) {
  $("text").disabled = !on;
  $("text-form").querySelector("button").disabled = !on;
}

$("main").addEventListener("click", () => (agent ? stop() : start()));
$("text-form").addEventListener("submit", (e) => {
  e.preventDefault();
  agent?.say($("text").value);
  $("text").value = "";
});

// Phones opened from a link like /#code=XYZ remember the code.
const fromLink = location.hash.match(/code=([^&]+)/);
if (fromLink) {
  accessCode.set(decodeURIComponent(fromLink[1]));
  history.replaceState(null, "", location.pathname);
}
