/*
 * Content script for ChatGPT and Perplexity.
 *
 * It listens on `window` in the capture phase, so it sees every send and every file
 * before the site's own handlers do. When something may contain WALLED_SENSITIVE it
 * stops the event, asks the desktop agent (which shows the native dialog), and then
 * either replays the original action unchanged (Continue) or leaves the page as it was
 * so the user can edit and retry (Cancel).
 *
 *   prompts: Enter in the composer · click on the send button · form submit
 *   files:   <input type=file> change · drag-and-drop · paste
 *   look:    recolours assistant responses (colour set in the toolbar popup)
 *
 * Prompts without the marker are never delayed (the check is a local string search).
 * Files are always checked by the agent, because the marker can be inside a PDF or an image.
 */
"use strict";

(() => {
  const Sites = globalThis.WalledSites;
  const site = Sites && Sites.current();
  if (!site) return;

  const MARKER = "WALLED_SENSITIVE";
  const MAX_FILE_BYTES = 25 * 1024 * 1024;
  const MAX_BATCH_BYTES = 40 * 1024 * 1024;      // stay well under Chrome's message size limit
  const LONG_PASTE = 2000;                        // long pastes may be turned into attachments
  const SUPPORTED = /\.(txt|md|markdown|csv|tsv|json|jsonl|xml|html?|log|ya?ml|ini|cfg|conf|toml|env|py|js|ts|tsx|jsx|java|c|cc|cpp|h|hpp|cs|go|rs|rb|php|sh|sql|r|swift|kt|tex|rtf|srt|vtt|pdf|png|jpe?g|gif|bmp|webp|tiff?|heic|heif)$/i;

  const settings = { recolor: true, color: "#d9480f" };   // protection itself cannot be switched off
  let bypass = false;          // true only while we replay an approved action
  let pending = false;         // a confirmation is on screen; hold further sends
  let approvedPaste = "";      // long pasted text the user already approved

  // ------------------------------------------------------------------ agent bridge
  async function askAgent(payload) {
    try {
      const reply = await chrome.runtime.sendMessage({ type: "agent", payload: { site: site.id, ...payload } });
      return reply && typeof reply === "object" ? reply : { ok: false, error: "no reply" };
    } catch (err) {
      // "Extension context invalidated" after an update/reload, or the worker is gone.
      return { ok: false, error: String((err && err.message) || err) };
    }
  }

  function withBypass(fn) {
    bypass = true;
    try { return fn(); } finally { bypass = false; }
  }

  function stop(event) {
    event.preventDefault();
    event.stopImmediatePropagation();
  }

  // ------------------------------------------------------------------ prompts
  // Rules come from the desktop app (keyword / built-in detector / regex), cached by the
  // background worker. Until the first sync, the WALLED_SENSITIVE marker rule applies.
  let rules = [{ id: "marker", kind: "keyword", pattern: MARKER, case: true }];
  let compiled = compile(rules);

  function luhn(v) {
    const d = v.replace(/\D/g, "").split("").map(Number);
    if (d.length < 13 || d.length > 19 || new Set(d).size === 1) return false;
    let sum = 0;
    d.reverse().forEach((x, i) => { if (i % 2) { x *= 2; if (x > 9) x -= 9; } sum += x; });
    return sum % 10 === 0;
  }
  const VD = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],[3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],
              [5,9,8,7,6,0,4,3,2,1],[6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],[9,8,7,6,5,4,3,2,1,0]];
  const VP = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],[8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],
              [4,2,8,6,5,7,3,9,0,1],[2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]];
  function verhoeff(v) {
    const d = v.replace(/\D/g, "").split("").map(Number);
    if (d.length !== 12) return false;
    let c = 0;
    d.reverse().forEach((x, i) => { c = VD[c][VP[i % 8][x]]; });
    return c === 0;
  }
  const VALIDATORS = { luhn, verhoeff };

  function compile(list) {
    const out = [];
    for (const r of list) {
      if (r.kind === "keyword") out.push({ test: (t) => (r.case ? t.includes(r.pattern) : t.toLowerCase().includes(r.pattern.toLowerCase())) });
      else if (r.kind === "regex") {
        try {
          const re = new RegExp(r.pattern, (r.flags || "").replace(/[^imsu]/g, "") + "g");
          const check = VALIDATORS[r.validator];
          out.push({ test: (t) => { re.lastIndex = 0; for (const m of t.matchAll(re)) if (!check || check(m[0])) return true; return false; } });
        } catch { /* a pattern this browser cannot compile: the agent still checks files */ }
      }
    }
    return out;
  }

  function hasMarker(text) {          // "is anything sensitive in this text?" (name kept from v1)
    if (!text) return false;
    if (approvedPaste && text.includes(approvedPaste)) text = text.split(approvedPaste).join(" ");
    return compiled.some((r) => r.test(text));
  }

  async function syncRules() {
    try {
      const reply = await chrome.runtime.sendMessage({ type: "rules" });
      if (reply && Array.isArray(reply.rules) && reply.rules.length) { rules = reply.rules; compiled = compile(rules); }
    } catch { /* worker restarting: keep the current rules */ }
  }
  syncRules();
  const syncTimer = setInterval(() => {
    if (!chrome.runtime || !chrome.runtime.id) { clearInterval(syncTimer); return; }   // extension updated/removed
    syncRules();
  }, 60_000);

  function textNear(button) {
    const form = button.closest("form");
    const scoped = form && site.composer.map((s) => form.querySelector(s)).find(Boolean);
    const active = document.activeElement;
    const editing = active && (active.isContentEditable || active.tagName === "TEXTAREA") ? active : null;
    return Sites.composerText(scoped || editing || Sites.composerFor(site, null));
  }

  async function confirmPrompt(text, replay, composer) {
    pending = true;
    let reply;
    try {
      reply = await askAgent({ type: "confirm_prompt", text });
    } finally {
      pending = false;
    }
    if (!reply.ok) {
      toast("Prompt blocked: the Walled Guard desktop agent is not reachable. Run “walled-guard status”.", "error");
      return;
    }
    if (reply.decision === "cancel") {
      toast(reply.via === "rule"
        ? `Not sent: blocked by your rule (${(reply.hits || []).map((h) => h.name).join(", ")}). Edit the prompt and send again.`
        : reply.via === "phone" ? "Not sent: blocked from Telegram. Your prompt is still in the box."
        : "Not sent. Your prompt is still in the box — edit it and send again.", "warn");
      focus(composer);
      return;
    }
    if (reply.decision === "allow" && reply.via === "rule") {
      toast(`Sent with a warning: ${(reply.hits || []).map((h) => h.name).join(", ")}.`, "warn");
    }
    // "continue" (user approved) or "allow" (nothing to ask about after all)
    const now = Sites.composerText(composer || Sites.composerFor(site, null));
    if (composer && now.trim() !== text.trim()) {
      toast("The prompt changed while the dialog was open, so it was not sent. Press send again.", "warn");
      return;
    }
    withBypass(replay);
  }

  function focus(el) {
    try { el && el.focus(); } catch { /* element gone */ }
  }

  function replaySend(composer) {
    const button = Sites.findSendButton(site);
    if (button && !button.disabled) {
      button.click();
      return;
    }
    const target = composer || Sites.composerFor(site, null);
    if (!target) return;
    const init = { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true, composed: true };
    target.dispatchEvent(new KeyboardEvent("keydown", init));
    target.dispatchEvent(new KeyboardEvent("keypress", init));
    target.dispatchEvent(new KeyboardEvent("keyup", init));
  }

  window.addEventListener("keydown", (event) => {
    if (bypass) return;
    if (event.key !== "Enter" || event.shiftKey || event.altKey || event.isComposing || event.keyCode === 229) return;
    const composer = Sites.composerFor(site, event.target);
    if (!composer) return;
    if (pending) { stop(event); return; }
    const text = Sites.composerText(composer);
    if (!hasMarker(text)) return;
    stop(event);
    confirmPrompt(text, () => replaySend(composer), composer);
  }, true);

  // pointerdown/mousedown are only held while a dialog is open, so a double-click can't slip through.
  for (const type of ["pointerdown", "mousedown"]) {
    window.addEventListener(type, (event) => {
      if (bypass || !pending) return;
      if (Sites.sendButtonFor(site, event.target)) stop(event);
    }, true);
  }

  window.addEventListener("click", (event) => {
    if (bypass) return;
    const button = Sites.sendButtonFor(site, event.target);
    if (!button) return;
    if (pending) { stop(event); return; }
    const text = textNear(button);
    if (!hasMarker(text)) return;
    stop(event);
    const composer = Sites.composerFor(site, null);
    confirmPrompt(text, () => button.isConnected ? button.click() : replaySend(composer), composer);
  }, true);

  window.addEventListener("submit", (event) => {
    if (bypass) return;
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    const composer = site.composer.map((s) => form.querySelector(s)).find(Boolean);
    if (!composer) return;
    if (pending) { stop(event); return; }
    const text = Sites.composerText(composer);
    if (!hasMarker(text)) return;
    stop(event);
    confirmPrompt(text, () => form.requestSubmit(), composer);
  }, true);

  // ------------------------------------------------------------------ files
  function isSupported(file) {
    return SUPPORTED.test(file.name) || /^(text\/|image\/|application\/(pdf|json|xml))/.test(file.type)
      || (!file.type && file.size < 2 * 1024 * 1024);
  }

  function readBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).replace(/^data:[^,]*,/, ""));
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  }

  async function describe(files) {
    let budget = MAX_BATCH_BYTES;
    const out = [];
    for (const file of files) {
      const base = { name: file.name || "unnamed file", mime: file.type || "" };
      if (!isSupported(file)) { out.push({ ...base, skipped: "not a text, PDF or image file" }); continue; }
      if (file.size > MAX_FILE_BYTES) { out.push({ ...base, skipped: "larger than 25 MB" }); continue; }
      if (file.size > budget) { out.push({ ...base, skipped: "too many large files at once" }); continue; }
      try {
        out.push({ ...base, data: await readBase64(file) });
        budget -= file.size;
      } catch {
        out.push({ ...base, skipped: "the browser could not read it" });
      }
    }
    return out;
  }

  /**
   * Check files with the agent. Resolves to the files that may go to the site:
   * all of them (no marker, or Continue), only the clean ones (Cancel), or none (agent down).
   */
  async function guardFiles(files) {
    pending = true;
    const scanning = files.length ? toast(`Walled Guard is checking ${files.length === 1 ? "“" + files[0].name + "”" : files.length + " files"}…`, "info", 0) : null;
    let reply;
    try {
      reply = await askAgent({ type: "inspect_files", files: await describe(files) });
    } finally {
      pending = false;
      scanning && scanning.remove();
    }
    if (!reply.ok || !Array.isArray(reply.results)) {
      toast("Upload blocked: the Walled Guard desktop agent is not reachable. Run “walled-guard status”.", "error");
      return [];
    }
    if (reply.decision !== "cancel") return files;
    const blocked = new Set(reply.results.map((r, i) => (r.found || (r.error && ["text", "pdf", "image"].includes(r.kind)) ? i : -1)));
    const keep = files.filter((_, i) => !blocked.has(i));
    const removed = files.length - keep.length;
    toast(removed === files.length
      ? "Upload cancelled. Nothing was sent."
      : `Upload cancelled for ${removed} file${removed > 1 ? "s" : ""}; the other ${keep.length} continued.`, "warn");
    return keep;
  }

  function fileList(files) {
    const dt = new DataTransfer();
    files.forEach((f) => dt.items.add(f));
    return dt;
  }

  // <input type=file>: "input" and "change" both fire after the picker closes.
  window.addEventListener("input", (event) => {
    const el = event.target;
    if (bypass || !(el instanceof HTMLInputElement) || el.type !== "file") return;
    if (el.files && el.files.length) event.stopImmediatePropagation();   // handled on "change"
  }, true);

  window.addEventListener("change", async (event) => {
    const input = event.target;
    if (bypass || !(input instanceof HTMLInputElement) || input.type !== "file") return;
    if (!input.files || !input.files.length) return;
    event.stopImmediatePropagation();
    const files = [...input.files];
    const keep = await guardFiles(files);
    if (keep.length === 0) { input.value = ""; return; }
    if (keep.length !== files.length) input.files = fileList(keep).files;
    withBypass(() => {
      input.dispatchEvent(new Event("input", { bubbles: true, composed: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }, true);

  // Drag-and-drop. The DataTransfer is only readable during the event, so copy the files first.
  window.addEventListener("drop", async (event) => {
    if (bypass || !event.dataTransfer) return;
    const files = [...event.dataTransfer.files];
    if (!files.length) return;
    stop(event);
    const target = event.target instanceof Node && event.target.isConnected ? event.target : document.body;
    const where = { clientX: event.clientX, clientY: event.clientY, screenX: event.screenX, screenY: event.screenY };
    const keep = await guardFiles(files);
    withBypass(() => {
      if (!keep.length) {
        target.dispatchEvent(new DragEvent("dragleave", { bubbles: true, ...where }));   // clear "drop here" overlays
        return;
      }
      target.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, composed: true, dataTransfer: fileList(keep), ...where }));
    });
  }, true);

  // Paste: files (screenshots, copied files) and very long text, which sites may turn into an attachment.
  window.addEventListener("paste", async (event) => {
    if (bypass || !event.clipboardData) return;
    const files = [...event.clipboardData.files];
    const target = event.target instanceof Node && event.target.isConnected ? event.target : document.activeElement || document.body;
    if (files.length) {
      const text = event.clipboardData.getData("text/plain");
      stop(event);
      const keep = await guardFiles(files);
      if (!keep.length) return;
      const dt = fileList(keep);
      if (text) dt.setData("text/plain", text);
      withBypass(() => target.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, composed: true, clipboardData: dt })));
      return;
    }
    const text = event.clipboardData.getData("text/plain");
    if (text.length < LONG_PASTE || !hasMarker(text)) return;
    stop(event);
    pending = true;
    let reply;
    try { reply = await askAgent({ type: "confirm_prompt", text }); } finally { pending = false; }
    if (!reply.ok) { toast("Paste blocked: the Walled Guard desktop agent is not reachable.", "error"); return; }
    if (reply.decision === "cancel") { toast("Paste cancelled. Nothing was added.", "warn"); return; }
    approvedPaste = text;
    const dt = new DataTransfer();
    dt.setData("text/plain", text);
    withBypass(() => target.dispatchEvent(new ClipboardEvent("paste", { bubbles: true, cancelable: true, composed: true, clipboardData: dt })));
  }, true);

  // ------------------------------------------------------------------ response colour
  const style = document.createElement("style");
  style.id = "walled-guard-responses";
  const scope = site.responses.join(", ");
  style.textContent = `
    html.walled-recolor :is(${scope}),
    html.walled-recolor :is(${scope}) :not(pre, pre *, svg, svg *, .katex-html *) {
      color: var(--walled-response-color, #d9480f) !important;
    }
    html.walled-recolor :is(${scope}) :is(a, a *) {
      color: var(--walled-response-color, #d9480f) !important;
      text-decoration: underline !important;
    }`;
  (document.head || document.documentElement).appendChild(style);

  function applyLook() {
    const root = document.documentElement;
    root.classList.toggle("walled-recolor", settings.recolor);
    root.style.setProperty("--walled-response-color", settings.color);
  }

  // ------------------------------------------------------------------ in-page notices
  // Short status notes only. The confirmation itself is always the agent's native dialog.
  let toastRoot = null;
  function toast(message, tone = "info", ms = 5000) {
    if (!toastRoot) {
      const host = document.createElement("walled-guard-toasts");
      host.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:2147483647;pointer-events:none";
      toastRoot = host.attachShadow({ mode: "open" });
      toastRoot.innerHTML = `<style>
        .t{font:500 13px/1.4 system-ui,-apple-system,Segoe UI,sans-serif;color:#fff;background:#111827;border-radius:10px;
           padding:10px 14px;margin-top:8px;max-width:360px;box-shadow:0 8px 24px rgba(0,0,0,.25);border-left:4px solid #64748b;
           animation:in .18s ease-out}
        .info{border-color:#3b82f6}.warn{border-color:#f59e0b}.error{border-color:#ef4444}
        b{display:block;font-size:11px;letter-spacing:.06em;text-transform:uppercase;opacity:.7;margin-bottom:2px}
        @keyframes in{from{opacity:0;transform:translateY(6px)}}</style>`;
      document.documentElement.appendChild(host);
    }
    const el = document.createElement("div");
    el.className = `t ${tone}`;
    el.setAttribute("role", tone === "error" ? "alert" : "status");
    const title = document.createElement("b");
    title.textContent = "Walled Guard";
    el.append(title, document.createTextNode(message));
    toastRoot.appendChild(el);
    if (ms) setTimeout(() => el.remove(), ms);
    return el;
  }

  // ------------------------------------------------------------------ settings
  chrome.storage.sync.get(Object.keys(settings)).then((stored) => {
    for (const key of Object.keys(settings)) if (stored[key] !== undefined) settings[key] = stored[key];
    applyLook();
  }).catch(applyLook);
  applyLook();

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "sync") return;
    for (const [key, { newValue }] of Object.entries(changes)) {
      if (key in settings && newValue !== undefined) settings[key] = newValue;
    }
    applyLook();
  });
})();
