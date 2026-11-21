/*
 * Password helper (optional, all sites). Registered by the background worker only after the
 * user turns it on in the popup and grants the all-sites permission.
 *
 * When a password field gets focus, a small Walled chip appears next to it:
 *   · sign-up / new-password fields → "Suggest strong password" (generated here, never sent anywhere)
 *   · login fields                  → "Fill from Walled" (your encrypted vault, via the desktop app;
 *                                     each fill can require an Allow tap on Telegram)
 *   · a typed password               → "Save to Walled"
 *
 * The chip lives in a closed shadow root and only reacts to real (trusted) clicks, so the page
 * cannot press it. Which site a password belongs to is decided by the background worker from
 * the tab's real origin, not by this script or the page.
 */
"use strict";

(() => {
  if (window.__walledPasswords) return;
  window.__walledPasswords = true;

  const LOWER = "abcdefghijkmnopqrstuvwxyz", UPPER = "ABCDEFGHJKLMNPQRSTUVWXYZ", DIGITS = "23456789", SYMBOLS = "!@#$%^&*-_=+?";

  function generate(length = 20) {
    const pool = LOWER + UPPER + DIGITS + SYMBOLS;
    for (;;) {
      const bytes = crypto.getRandomValues(new Uint32Array(length));
      const pw = Array.from(bytes, (b) => pool[b % pool.length]).join("");
      if ([LOWER, UPPER, DIGITS, SYMBOLS].every((set) => [...pw].some((c) => set.includes(c)))) return pw;
    }
  }

  function setValue(input, value) {
    // React and friends track the native value setter; use it so the page notices the change.
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function visible(el) {
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden";
  }

  function passwordFields(scope) {
    return [...(scope || document).querySelectorAll("input[type=password]")].filter(visible);
  }

  function isNewPassword(input) {
    if (/new-password/.test(input.autocomplete)) return true;
    if (/current-password/.test(input.autocomplete)) return false;
    const form = input.form || input.closest("form") || document;
    if (passwordFields(form).length >= 2) return true;
    const words = `${location.pathname} ${form.textContent || ""}`.slice(0, 4000).toLowerCase();
    return /sign ?up|register|create (an )?account|join now|new password|choose a password/.test(words);
  }

  function usernameField(input) {
    const scope = input.form || input.closest("form") || document;
    const candidates = [...scope.querySelectorAll("input")].filter((el) => visible(el)
      && /^(email|text|tel)$/.test(el.type) && el !== input);
    const preferred = candidates.find((el) => /username|email/.test(el.autocomplete)
      || /user|email|login|account|phone/i.test(`${el.name} ${el.id} ${el.placeholder}`));
    if (preferred) return preferred;
    const before = candidates.filter((el) => el.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING);
    return before[before.length - 1] || null;
  }

  function agent(payload) {
    return chrome.runtime.sendMessage({ type: "agent", payload }).catch((e) => ({ ok: false, error: String(e.message || e) }));
  }

  // ------------------------------------------------------------------ chip UI
  const host = document.createElement("walled-password-chip");
  host.style.cssText = "position:absolute;z-index:2147483647;top:0;left:0;display:none";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = `<style>
    .box{font:500 12.5px/1.3 system-ui,-apple-system,Segoe UI,sans-serif;background:#0f172a;color:#fff;border-radius:10px;
         box-shadow:0 8px 24px rgba(0,0,0,.25);padding:6px;display:flex;flex-direction:column;gap:4px;min-width:210px}
    .head{display:flex;align-items:center;gap:6px;padding:2px 6px 4px;color:#94a3b8;font-size:11px;text-transform:uppercase;letter-spacing:.06em}
    .head i{width:8px;height:8px;border-radius:50%;background:#d9480f;display:inline-block}
    button{all:unset;cursor:pointer;padding:7px 9px;border-radius:7px;display:block}
    button:hover,button:focus-visible{background:#1e293b}
    .msg{padding:6px 9px;color:#cbd5e1;max-width:260px}
    .pw{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#fdba74;word-break:break-all}</style>
    <div class="box" part="box"><div class="head"><i></i>Walled</div><div id="items"></div></div>`;
  const items = root.getElementById("items");
  (document.body || document.documentElement).appendChild(host);

  let current = null;

  function place(input) {
    const r = input.getBoundingClientRect();
    host.style.top = `${window.scrollY + r.bottom + 6}px`;
    host.style.left = `${window.scrollX + Math.max(8, r.right - 220)}px`;
    host.style.display = "block";
  }
  function hide() { host.style.display = "none"; current = null; }
  function message(text) { items.replaceChildren(Object.assign(document.createElement("div"), { className: "msg", textContent: text })); }
  function button(label, fn, extra) {
    const b = document.createElement("button");
    b.textContent = label;
    if (extra) { const s = document.createElement("div"); s.className = "pw"; s.textContent = extra; b.append(s); }
    b.addEventListener("click", (e) => { if (!e.isTrusted) return; e.preventDefault(); fn(); });
    return b;
  }

  async function show(input) {
    current = input;
    place(input);
    const list = [];
    if (isNewPassword(input)) {
      const pw = generate();
      list.push(button("Use a strong password", () => {
        const fields = passwordFields(input.form || input.closest("form") || document);
        (fields.length >= 2 ? fields.slice(0, 2) : [input]).forEach((f) => setValue(f, pw));
        offerSave(input, pw);
      }, pw));
    } else {
      message("Looking in your vault…");
      const found = await agent({ type: "vault_find" });
      if (current !== input) return;
      if (found.ok && found.entries.length) {
        for (const e of found.entries.slice(0, 5)) list.push(button(`Fill ${e.username || "saved login"}`, () => fill(input, e)));
      } else if (found.locked) {
        list.push(Object.assign(document.createElement("div"), { className: "msg", textContent: "Vault locked — unlock it in the Walled app." }));
      } else if (!found.ok) {
        list.push(Object.assign(document.createElement("div"), { className: "msg", textContent: found.error || "Walled is not running." }));
      } else {
        list.push(Object.assign(document.createElement("div"), { className: "msg", textContent: "No saved login for this site." }));
      }
    }
    if (input.value) list.push(button("Save this password to Walled", () => save(input, input.value)));
    items.replaceChildren(...list);
  }

  async function fill(input, entry) {
    message("Waiting for approval… (check Telegram if fills need a tap)");
    const reply = await agent({ type: "vault_fill", entry: entry.id });
    if (!reply.ok) { message(reply.error || "Fill failed."); return; }
    const user = usernameField(input);
    if (user && reply.username && !user.value) setValue(user, reply.username);
    setValue(input, reply.password);
    hide();
  }

  function offerSave(input, pw) {
    items.replaceChildren(button("Save to Walled vault", () => save(input, pw)),
      Object.assign(document.createElement("div"), { className: "msg", textContent: "Filled. Save it so Walled can fill it next time." }));
  }

  async function save(input, pw) {
    const user = usernameField(input);
    const reply = await agent({ type: "vault_save", username: user ? user.value : "", password: pw });
    message(reply.ok ? "Saved to your Walled vault." : reply.error || "Could not save.");
    setTimeout(() => { if (!host.matches(":hover")) hide(); }, 2500);
  }

  document.addEventListener("focusin", (e) => {
    const el = e.target;
    if (el instanceof HTMLInputElement && el.type === "password" && el !== current) show(el);
  }, true);
  document.addEventListener("mousedown", (e) => {
    if (current && e.target !== host && e.target !== current) hide();
  }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); }, true);
  window.addEventListener("resize", () => current && place(current));
})();
