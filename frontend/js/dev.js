// Developer panel: the skills Mario learned through Claude Code, their code and
// how often they ran. Skill files are written by Claude, so everything is put
// on the page as text, never as HTML.

import { ApiError, devSkills } from "./api.js";

const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

const fmtDate = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("tr-TR", { dateStyle: "medium", timeStyle: "short" });
};

function field(dl, label, value) {
  if (value == null || value === "") return;
  dl.append(el("dt", null, label));
  const dd = el("dd");
  if (value instanceof Node) dd.append(value);
  else dd.textContent = value;
  dl.append(dd);
}

function card(s) {
  const d = el("details", "skill" + (s.valid ? "" : " broken"));
  const sum = el("summary");
  const head = el("div", "skill-head");
  head.append(el("code", "skill-name", s.name));
  if (!s.valid) head.append(el("span", "tag bad", "Bozuk"));
  else if (s.confirm) head.append(el("span", "tag warn", "Onay ister"));
  if (s.valid) head.append(el("span", "tag", s.entry.endsWith(".py") ? "Python" : "PowerShell"));
  const runs = s.usage?.runs ?? 0;
  head.append(el("span", "skill-runs", runs ? `${runs} kez çalıştı` : "Hiç çalışmadı"));
  sum.append(head, el("p", "skill-desc", s.valid ? s.description : s.error));
  d.append(sum);

  const body = el("div", "skill-body");
  const dl = el("dl");
  if (s.valid) {
    const params = Object.entries(s.parameters || {});
    if (params.length) {
      const ul = el("ul", "params");
      for (const [k, v] of params) {
        const li = el("li");
        li.append(el("code", null, k));
        if (s.required.includes(k)) li.append(el("span", "req", " zorunlu"));
        li.append(el("span", null, ` — ${v}`));
        ul.append(li);
      }
      field(dl, "Parametreler", ul);
    } else field(dl, "Parametreler", "Yok");
    field(dl, "Örnek", s.example ? `«${s.example}»` : "");
    field(dl, "Onay sorusu", s.confirm);
  }
  field(dl, "Güncellendi", fmtDate(s.updated));
  const u = s.usage || {};
  if (u.runs) {
    field(dl, "Son çalışma", `${fmtDate(u.last_run)} · ${u.last_status === "ok" ? "başarılı" : "hata"}`);
    field(dl, "Son yanıt", u.last_message);
    if (u.errors) field(dl, "Hatalar", `${u.errors} / ${u.runs}`);
  }
  body.append(dl);

  const code = s.valid ? s.source : s.manifest;
  if (code) {
    body.append(el("p", "file-name", s.valid ? `skills/${s.name}/${s.entry}` : `skills/${s.name}/skill.json`));
    body.append(el("pre", "source", code));
  }
  d.append(body);
  return d;
}

async function load(askCode) {
  const list = $("dev-list");
  const summary = $("dev-summary");
  summary.textContent = "Yükleniyor…";
  let data;
  try {
    data = await devSkills();
  } catch (err) {
    if (err instanceof ApiError && err.status === 401 && (await askCode())) return load(askCode);
    summary.textContent = err.message;
    list.replaceChildren();
    return;
  }
  const ok = data.skills.filter((s) => s.valid).length;
  const broken = data.skills.length - ok;
  const runs = data.skills.reduce((n, s) => n + (s.usage?.runs ?? 0), 0);
  summary.textContent = data.skills.length
    ? `${ok} yetenek${broken ? `, ${broken} bozuk` : ""} · toplam ${runs} çalıştırma · ${data.dir}`
    : `Henüz öğrenilmiş yetenek yok. Mario'ya «bunu öğren» dediğinde burada görünür. (${data.dir})`;
  list.replaceChildren(...data.skills.map(card));

  $("dev-forgotten-wrap").hidden = !data.forgotten.length;
  $("dev-forgotten").replaceChildren(...data.forgotten.map((n) => el("li", null, n)));
}

export function initDev(askCode) {
  const dialog = $("dev");
  const open = () => {
    $("settings").close();
    dialog.showModal();
    load(askCode);
  };
  for (const b of document.querySelectorAll("[data-open-dev]")) b.addEventListener("click", open);
  $("dev-refresh").addEventListener("click", () => load(askCode));
}
