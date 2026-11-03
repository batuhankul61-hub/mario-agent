// Demo panel for the concept pages: switches Mario's state, the confirmation alert and
// light/dark. Not part of any design; the real app drives the same attributes.

const STATUS = { idle: "Hazır", listening: "Dinliyor", speaking: "Konuşuyor", working: "Çalışıyor" };
const root = document.documentElement;
const params = new URLSearchParams(location.search);

function setState(s) {
  root.dataset.state = s;
  for (const el of document.querySelectorAll("[data-status]")) el.textContent = STATUS[s];
  mark("state", s);
}
function setTheme(t) {
  root.dataset.theme = t;
  mark("theme", t);
}
function setConfirm(on) {
  root.toggleAttribute("data-confirm", on);
  mark("confirm", on ? "on" : "off");
}
function mark(group, value) {
  for (const b of document.querySelectorAll(`.demo [data-${group}]`)) b.setAttribute("aria-pressed", String(b.dataset[group] === value));
}

const panel = document.createElement("div");
panel.className = "demo";
panel.innerHTML = `
  <b>Durum</b>
  ${Object.entries(STATUS).map(([k, v]) => `<button data-state="${k}">${v}</button>`).join("")}
  <span class="gap"></span>
  <button data-confirm="on">Onay</button>
  <span class="gap"></span>
  <button data-theme="light">Açık</button><button data-theme="dark">Koyu</button>`;
panel.addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  if (b.dataset.state) setState(b.dataset.state);
  if (b.dataset.theme) setTheme(b.dataset.theme);
  if (b.dataset.confirm) setConfirm(!root.hasAttribute("data-confirm"));
});
if (!params.has("bare")) document.body.append(panel);

// Alert buttons in the concepts close it.
document.addEventListener("click", (e) => {
  if (e.target.closest(".alert button")) setConfirm(false);
});

setState(params.get("state") ?? "listening");
setTheme(params.get("theme") ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
setConfirm(params.has("confirm"));
