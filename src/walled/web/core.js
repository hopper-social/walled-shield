/* Walled Control Center: shared helpers. Plain DOM, no framework; every value from the API is
 * set with textContent (never innerHTML), so nothing from rules, logs or the vault can inject markup. */
"use strict";

// ------------------------------------------------------------------ session
// The window opens with a one-time launch ticket in the URL fragment (never sent to a server or
// logged). It is traded once for a session token, kept only for this window.
let TOKEN = sessionStorage.getItem("walled-token") || "";
const COMPACT = /compact=1/.test(location.hash) || sessionStorage.getItem("walled-compact") === "1";
if (COMPACT) sessionStorage.setItem("walled-compact", "1");

async function redeemTicket() {
  const m = location.hash.match(/k=([\w-]+)/);
  const page = (location.hash.match(/p=(\w+)/) || [])[1];
  if (!m) return;
  history.replaceState(null, "", location.pathname + "#" + (page || "home"));
  const res = await fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ticket: m[1] }) });
  const data = await res.json().catch(() => ({}));
  if (res.ok && data.token) { TOKEN = data.token; sessionStorage.setItem("walled-token", TOKEN); }
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || (opts.body ? "POST" : "GET"),
    headers: { "X-Walled-Token": TOKEN, "Content-Type": "application/json" },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

// ------------------------------------------------------------------ DOM
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;          // CSSOM: allowed by the page's strict CSP
    else if (k === "value") el.value = v;
    else if (k === "checked") el.checked = !!v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false)
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return el;
}
const $main = () => document.getElementById("main");

const ICONS = {
  home: "M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z",
  shield: "M12 3 4 6v6c0 4.4 3.4 8.4 8 9 4.6-.6 8-4.6 8-9V6z",
  send: "M21 3 3 10.5l7 2.5 2.5 7z M10 13l5-5",
  spark: "M12 2v5M12 17v5M2 12h5M17 12h5M5 5l3 3M16 16l3 3M19 5l-3 3M8 16l-3 3",
  globe: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM3 12h18M12 3c2.5 2.5 3.5 6 3.5 9s-1 6.5-3.5 9c-2.5-2.5-3.5-6-3.5-9s1-6.5 3.5-9z",
  key: "M15 7a4 4 0 1 1-3.9 5H8v3H5v-3H3v-2h8.1A4 4 0 0 1 15 7z",
  flask: "M9 3h6M10 3v6L4.5 19a1.5 1.5 0 0 0 1.3 2h12.4a1.5 1.5 0 0 0 1.3-2L14 9V3M7 14h10",
  pulse: "M3 12h4l3-8 4 16 3-8h4",
  gear: "M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
  check: "M5 12.5 10 17 19 7",
  lock: "M6 11h12v10H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3",
  plus: "M12 5v14M5 12h14",
  id: "M3 6h18v12H3zM7 10h4M7 14h6M15.5 10.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z",
  card: "M3 6h18v12H3zM3 10h18M7 15h3",
  file: "M6 3h8l4 4v14H6zM14 3v4h4",
  word: "M4 7V5h16v2M12 5v14M9 19h6",
  code: "M8 8 4 12l4 4M16 8l4 4-4 4M14 5l-4 14",
  bolt: "M13 3 5 13h6l-1 8 8-10h-6z",
  eye: "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
  phone: "M8 3h8a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1zM11 18h2",
  clock: "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM12 7v5l3 2",
  bell: "M6 16V11a6 6 0 1 1 12 0v5l2 2H4zM10 20a2 2 0 0 0 4 0",
  chev: "M9 6l6 6-6 6",
  bulb: "M9 18h6M10 21h4M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2V16h5v-.1c0-.8.4-1.5 1-2A6 6 0 0 0 12 3z",
  dots: "M5 12h.01M12 12h.01M19 12h.01",
};
function icon(name, size = 18) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24"); svg.setAttribute("width", size); svg.setAttribute("height", size);
  svg.setAttribute("class", "ico"); svg.setAttribute("aria-hidden", "true");
  const p = document.createElementNS(ns, "path"); p.setAttribute("d", ICONS[name] || ICONS.shield);
  svg.append(p);
  return svg;
}

function toast(msg, err = false) {
  const box = document.getElementById("toast");
  if (!box || !msg) return;
  const el = h("div", { class: err ? "err" : "" }, msg);
  box.append(el);
  setTimeout(() => el.remove(), err ? 6000 : 3200);
}
async function run(fn, ok) {
  try { const r = await fn(); if (ok) toast(ok); return r; } catch (e) { toast(e.message, true); throw e; }
}
function sw(checked, onchange, label) {
  return h("label", { class: "switch", title: label || "" },
    h("input", { type: "checkbox", checked, "aria-label": label || "toggle", onchange: (e) => onchange(e.target.checked) }), h("i"));
}
function sel(value, options, onchange, label) {
  return h("select", { "aria-label": label || "", onchange: (e) => onchange(e.target.value) },
    options.map(([v, t]) => { const o = h("option", { value: v }, t); if (v === value) o.selected = true; return o; }));
}
/** Segmented control: [[value, label, hint]] */
function seg(value, options, onchange, label) {
  const wrap = h("div", { class: "seg", role: "radiogroup", "aria-label": label || "" });
  for (const [v, t, hint] of options) {
    const b = h("button", { type: "button", role: "radio", "aria-checked": String(v === value), class: v === value ? "on" : "", title: hint || "",
      onclick: () => { wrap.querySelectorAll("button").forEach((x) => { x.classList.remove("on"); x.setAttribute("aria-checked", "false"); });
        b.classList.add("on"); b.setAttribute("aria-checked", "true"); onchange(v); } }, t);
    wrap.append(b);
  }
  return wrap;
}

/** A secret field that browsers' password managers won't offer to save (it isn't type=password). */
function secretInput(attrs = {}) {
  const input = h("input", { type: "text", class: "secret", autocomplete: "off", autocapitalize: "off", spellcheck: "false",
    "data-1p-ignore": "", "data-lpignore": "true", "data-bwignore": "", "data-form-type": "other", ...attrs });
  const eye = h("button", { type: "button", class: "ghost eye", title: "Show", "aria-label": "Show or hide",
    onclick: () => { input.classList.toggle("shown"); } }, icon("eye", 16));
  const wrap = h("div", { class: "secret-wrap" }, input, eye);
  wrap.input = input;
  return wrap;
}

/** Page header with icon, title, subtitle and optional actions, then the body. */
function page(title, lede, ...body) {
  let iconName = "shield", actions = null;
  if (typeof lede === "object" && lede && !(lede instanceof Node)) ({ text: lede, icon: iconName = "shield", actions = null } = lede);
  const head = h("header", { class: "page-head" },
    h("div", { class: "page-icon" }, icon(iconName, 20)),
    h("div", { class: "page-titles" }, h("h1", {}, title), lede ? h("p", { class: "lede" }, lede) : null),
    actions ? h("div", { class: "page-actions" }, actions) : null);
  $main().replaceChildren(head, ...body.filter((x) => x !== null && x !== undefined && x !== false));
  $main().focus({ preventScroll: true });
}
function skeleton(rows = 3) {
  $main().replaceChildren(h("div", { class: "skeleton" }, h("i", { class: "sk-title" }),
    ...Array.from({ length: rows }, () => h("i", { class: "sk-card" }))));
}
function card(...kids) { return h("section", { class: "card" }, ...kids); }
function section(title, sub, ...kids) {
  return h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, title), sub ? h("p", { class: "muted small" }, sub) : null), ...kids);
}
function fmtTime(t) { if (!t) return ""; const d = new Date(String(t).replace(/([+-]\d\d)(\d\d)$/, "$1:$2")); return isNaN(d) ? t : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }

// ------------------------------------------------------------------ live events (service → app)
let lastEvent = 0, eventsPrimed = false;
const eventListeners = [];
function onEvent(fn) { eventListeners.push(fn); }
async function pollEvents() {
  try {
    const r = await api(`/api/events?since=${lastEvent}`);
    if (!eventsPrimed) { eventsPrimed = true; lastEvent = r.last; }
    else for (const e of r.events) {
      lastEvent = Math.max(lastEvent, e.id);
      if (!e.type.startsWith("assist.") && !document.body.classList.contains("is-home")) toast(e.title, e.tone === "bad");
      eventListeners.forEach((fn) => { try { fn(e); } catch { /* listener errors never break polling */ } });
    }
  } catch { /* service restarting */ }
  setTimeout(pollEvents, 2000);
}

// ------------------------------------------------------------------ motion helpers
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

function confetti(count = 70) {
  if (REDUCED) return;
  const layer = h("div", { class: "confetti", "aria-hidden": "true" });
  const colors = ["#0d9488", "#2dd4bf", "#16a34a", "#2563eb", "#f2a33a", "#7c3aed"];
  for (let i = 0; i < count; i++) {
    layer.append(h("i", { style: `left:${Math.random() * 100}%;background:${colors[i % colors.length]};` +
      `animation-delay:${Math.random() * 0.4}s;animation-duration:${1.6 + Math.random() * 1.4}s;` +
      `transform:rotate(${Math.random() * 360}deg);width:${6 + Math.random() * 6}px;height:${8 + Math.random() * 8}px` }));
  }
  document.body.append(layer);
  setTimeout(() => layer.remove(), 3600);
}

function xpPop(points, anchor) {
  if (!points) return;
  const el = h("div", { class: "xp-pop" }, `+${points} XP`);
  (anchor || document.body).append(el);
  setTimeout(() => el.remove(), 1400);
}

function checkMark(size = 64) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 52 52"); svg.setAttribute("width", size); svg.setAttribute("height", size);
  svg.setAttribute("class", "check-anim");
  const c = document.createElementNS(ns, "circle"); c.setAttribute("cx", 26); c.setAttribute("cy", 26); c.setAttribute("r", 24);
  const p = document.createElementNS(ns, "path"); p.setAttribute("d", "M14 27 l8 8 l16 -17");
  svg.append(c, p);
  return svg;
}

function countdownRing(total = 120) {
  const ns = "http://www.w3.org/2000/svg";
  const wrap = h("div", { class: "ring" });
  const svg = document.createElementNS(ns, "svg"); svg.setAttribute("viewBox", "0 0 44 44");
  const bg = document.createElementNS(ns, "circle"); bg.setAttribute("cx", 22); bg.setAttribute("cy", 22); bg.setAttribute("r", 19); bg.setAttribute("class", "ring-bg");
  const fg = document.createElementNS(ns, "circle"); fg.setAttribute("cx", 22); fg.setAttribute("cy", 22); fg.setAttribute("r", 19); fg.setAttribute("class", "ring-fg");
  svg.append(bg, fg);
  const label = h("span", {}, "2:00");
  wrap.append(svg, label);
  const C = 2 * Math.PI * 19;
  fg.style.strokeDasharray = `${C}`;
  wrap.set = (left) => {
    fg.style.strokeDashoffset = `${C * (1 - Math.max(0, left) / total)}`;
    label.textContent = `${Math.floor(left / 60)}:${String(Math.max(0, left) % 60).padStart(2, "0")}`;
    wrap.classList.toggle("low", left < 30);
  };
  return wrap;
}

function scoreRing(percent, size = 132) {
  const ns = "http://www.w3.org/2000/svg";
  const wrap = h("div", { class: "score-ring", style: `width:${size}px;height:${size}px` });
  const svg = document.createElementNS(ns, "svg"); svg.setAttribute("viewBox", "0 0 120 120");
  const C = 2 * Math.PI * 52;
  for (const cls of ["sr-bg", "sr-fg"]) {
    const c = document.createElementNS(ns, "circle");
    c.setAttribute("cx", 60); c.setAttribute("cy", 60); c.setAttribute("r", 52); c.setAttribute("class", cls);
    if (cls === "sr-fg") { c.style.strokeDasharray = `${C}`; c.style.strokeDashoffset = `${C}`;
      requestAnimationFrame(() => requestAnimationFrame(() => { c.style.strokeDashoffset = `${C * (1 - percent / 100)}`; })); }
    svg.append(c);
  }
  wrap.append(svg, h("div", { class: "sr-label" }, h("b", {}, `${percent}`), h("small", {}, "/ 100")));
  return wrap;
}

function ago(ts) {
  if (!ts) return "never";
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  return s < 60 ? "just now" : s < 3600 ? `${Math.round(s / 60)} min ago` : s < 86400 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} d ago`;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function copyBtn(text, label = "Copy") {
  return h("button", { type: "button", class: "ghost", onclick: () => navigator.clipboard.writeText(typeof text === "function" ? text() : text).then(() => toast("Copied")) }, label);
}

// Short, memorable vault passphrases (4 random words); generated locally with crypto.getRandomValues.
const WORDS = ("amber anchor apple arrow aspen atlas bamboo banjo basil beacon birch bison blaze bloom breeze brook cactus camel candle canyon cedar cherry cider citrus cobalt comet coral cosmic cotton cricket crystal cypress dahlia delta desert dolphin dragon drift eagle ember falcon fern fiddle flint forest fossil galaxy garnet gecko ginger glacier granite harbor hazel heron honey horizon husky iris island ivory jasmine jungle juniper kayak kettle kiwi lagoon lantern lava lemon lilac linen lotus lunar magnet mango maple marble meadow meteor mint monsoon mosaic nectar nimbus nova oasis ocean olive onyx orbit orchid otter panda papaya pebble pepper pine pixel planet plum polar poppy prism quartz quill rapids raven reef river rocket saffron sage salmon sapphire satin shadow sierra silver sky sonic spruce storm summit sunset tango thunder tiger timber topaz tulip tundra valley velvet violet walnut willow winter yonder zebra zephyr").split(" ");
function passphrase(n = 4) {
  const r = crypto.getRandomValues(new Uint32Array(n + 1));
  return [...r.slice(0, n)].map((x) => WORDS[x % WORDS.length]).join("-") + "-" + (r[n] % 90 + 10);
}

/** The one-time "Load unpacked" step, as short as Chrome allows: one button, then 3 clicks. */
function extensionSteps(path) {
  const mac = /Mac/.test(navigator.platform);
  const note = h("div", { class: "muted small", role: "status" });
  const go = h("button", { class: "primary big", onclick: async () => {
    go.disabled = true;
    try {
      const r = (await api("/api/browser/extensions", { body: {} })).opened;
      note.textContent = `${r.browser ? `Opened ${r.browser}.` : "Open chrome://extensions in Chrome."} ${r.copied ? "The folder path is copied — just paste it." : ""}`;
      if (typeof pipFeel === "function") pipFeel("happy", null, 1500);
    } catch (e) { note.textContent = e.message; }
    go.disabled = false;
  } }, icon("globe", 18), "Open Chrome's extension page");
  return h("div", { class: "ext-steps" },
    h("div", { class: "row" }, go, note),
    h("ol", { class: "steps-mini" },
      h("li", {}, "Switch on ", h("b", {}, "Developer mode"), " (top right)."),
      h("li", {}, "Click ", h("b", {}, "Load unpacked"), mac ? [", press ", h("kbd", {}, "⌘⇧G"), ", ", h("kbd", {}, "⌘V"), ", Enter."] : ", paste the folder path, Enter."),
      h("li", {}, "Click ", h("b", {}, "Select"), ". Done — Pip waves on ", h("a", { href: "https://chatgpt.com", target: "_blank", rel: "noopener" }, "chatgpt.com"), ".")),
    h("details", { class: "muted small" }, h("summary", {}, "Path not copied?"),
      h("div", { class: "row", style: "margin-top:6px" }, h("code", {}, path), copyBtn(path, "Copy path"),
        h("button", { class: "ghost", onclick: () => api("/api/browser/reveal", { body: {} }) }, "Show folder"))));
}
