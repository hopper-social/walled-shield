/*
 * Walled companion: the small branded assistant that shows up on the page you're on
 * (ChatGPT, Perplexity, Claude, Telegram Web) so setup and protection never make you switch
 * windows.
 *
 *  · live cards for what just happened ("Telegram paired", "Vault created", "Test passed")
 *  · a one-time welcome on each protected site
 *  · secret-copy guard: copying a token or key shows a card and clears the clipboard after 60 s
 *    (and a Telegram bot token can be connected to Walled in one click)
 *
 * Exposed as globalThis.WalledCompanion for the Telegram assistant.
 */
"use strict";

(() => {
  if (globalThis.WalledCompanion) return;

  const BRAND = "#0d9488";
  const host = document.createElement("walled-companion");
  host.style.cssText = "position:fixed;top:16px;right:16px;z-index:2147483646;display:flex;flex-direction:column;gap:10px;width:340px;max-width:calc(100vw - 32px);pointer-events:none";
  const root = host.attachShadow({ mode: "open" });   // open: no secrets are shown here; clicks still need isTrusted
  const TONE_FACE = { ok: "thumbs", warn: "alert", bad: "sad", info: "happy" };      // Pip's face per card tone
  root.innerHTML = `<style>${globalThis.PipArt ? PipArt.CSS : ""}
    :host{all:initial}
    .card{pointer-events:auto;font:500 13.5px/1.45 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;color:#e8ecf4;
          background:#111a2b;border:1px solid #26354d;border-radius:16px;padding:14px 16px;
          box-shadow:0 18px 40px rgba(5,10,20,.35);animation:in .28s cubic-bezier(.2,.8,.2,1) both;position:relative;overflow:hidden}
    .card::before{content:"";position:absolute;inset:0 0 auto 0;height:3px;background:var(--tone,${BRAND})}
    .ok{--tone:#19c37d}.warn{--tone:#ffb020}.bad{--tone:#ff4d4f}.info{--tone:#2dd4bf}
    .head{display:flex;align-items:center;gap:8px;font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#8b97b0;font-weight:700}
    .head .pa{--pa-size:34px;flex:none;margin:-6px 0 -6px -4px}.bad .head .pa{animation:shake .45s ease-in-out 3}
    .card.poof{animation:poof .5s cubic-bezier(.6,0,.4,1) forwards;pointer-events:none}
    .x{margin-left:auto;cursor:pointer;color:#8b97b0;font-size:16px;line-height:1;background:none;border:0;padding:0 2px}
    h3{margin:8px 0 2px;font-size:15px;color:#fff;font-weight:700;letter-spacing:-.01em}
    p{margin:0;color:#b8c2d6}
    .row{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
    button.b{all:unset;cursor:pointer;padding:8px 14px;border-radius:10px;font-weight:700;font-size:13px;background:#1f2a42;color:#e8ecf4}
    button.b.primary{background:${BRAND};color:#fff}
    button.b:hover{filter:brightness(1.1)}
    input{all:unset;box-sizing:border-box;width:100%;margin-top:10px;padding:8px 10px;border-radius:9px;background:#0b1220;border:1px solid #2c3854;color:#fff;font:13px ui-monospace,Menlo,monospace}
    .steps{display:flex;gap:4px;margin-top:10px}.steps i{flex:1;height:4px;border-radius:9px;background:#26314a}.steps i.on{background:${BRAND}}.steps i.done{background:#19c37d}
    .check{display:inline-grid;place-items:center;width:22px;height:22px;border-radius:50%;background:#19c37d;color:#fff;font-size:13px;margin-right:6px;animation:pop .4s ease-out}
    code{font:12px ui-monospace,Menlo,monospace;background:#1f2a42;padding:1px 6px;border-radius:6px;color:#ffd2bf}
    @keyframes in{from{opacity:0;transform:translateY(-8px) scale(.98)}}
    @keyframes bob{50%{transform:translateY(-2px)}}
    @keyframes blink{0%,46%,50%,100%{transform:scaleY(1)}48%{transform:scaleY(.1)}}
    @keyframes shake{25%{transform:translateX(-2px) rotate(-6deg)}75%{transform:translateX(2px) rotate(6deg)}}
    @keyframes poof{40%{transform:scale(1.02)}100%{opacity:0;transform:translate(120px,-30px) scale(.1);filter:blur(6px)}}
    @keyframes pop{0%{transform:scale(.4)}70%{transform:scale(1.15)}100%{transform:scale(1)}}
    @media (prefers-reduced-motion: reduce){*{animation:none!important}}
  </style><div id="stack" style="display:flex;flex-direction:column;gap:10px"></div>`;
  const stack = root.getElementById("stack");
  const mount = () => (document.body || document.documentElement).appendChild(host);
  if (document.body) mount(); else document.addEventListener("DOMContentLoaded", mount, { once: true });

  /**
   * show({title, text, tone, steps:[done,total], input:{value,label}, actions:[{label, primary, run}], ms, id})
   * Returns the card element; cards with the same id replace each other.
   */
  /** Pip zips off with the card instead of it just disappearing. */
  function poof(card) {
    if (!card.isConnected || card.classList.contains("poof")) return;
    card.classList.add("poof");
    setTimeout(() => card.remove(), 520);
  }

  function show(opts) {
    const card = document.createElement("div");
    card.className = `card ${opts.tone || "info"}`;
    if (opts.id) {
      const old = stack.querySelector(`[data-id="${CSS.escape(opts.id)}"]`);
      if (old) old.remove();
      card.dataset.id = opts.id;
    }
    const head = document.createElement("div");
    head.className = "head";
    head.innerHTML = (globalThis.PipArt ? PipArt.markup(TONE_FACE[opts.tone] || "happy", { live: true }) : "") + "<span>Walled · Pip</span>";   // fixed templates only
    const x = document.createElement("button");
    x.className = "x"; x.textContent = "×"; x.title = "Close";
    x.addEventListener("click", () => poof(card));
    head.append(x);
    const h3 = document.createElement("h3");
    if (opts.tone === "ok") { const c = document.createElement("span"); c.className = "check"; c.textContent = "✓"; h3.append(c); }
    h3.append(document.createTextNode(opts.title || ""));
    card.append(head, h3);
    if (opts.text) { const p = document.createElement("p"); p.textContent = opts.text; card.append(p); }
    if (opts.code) { const p = document.createElement("p"); p.style.marginTop = "8px"; const c = document.createElement("code"); c.textContent = opts.code; p.append(c); card.append(p); }
    if (opts.steps) {
      const bar = document.createElement("div"); bar.className = "steps";
      for (let i = 0; i < opts.steps[1]; i++) { const s = document.createElement("i"); s.className = i < opts.steps[0] ? "done" : i === opts.steps[0] ? "on" : ""; bar.append(s); }
      card.append(bar);
    }
    let input = null;
    if (opts.input) {
      input = document.createElement("input");
      input.value = opts.input.value || ""; input.setAttribute("aria-label", opts.input.label || "value");
      input.setAttribute("autocomplete", "off"); input.setAttribute("data-1p-ignore", ""); input.setAttribute("data-lpignore", "true");
      card.append(input);
    }
    if (opts.actions && opts.actions.length) {
      const row = document.createElement("div"); row.className = "row";
      for (const a of opts.actions) {
        const b = document.createElement("button");
        b.className = `b ${a.primary ? "primary" : ""}`; b.textContent = a.label;
        b.addEventListener("click", (e) => {
          if (!e.isTrusted) return;                       // the page cannot press our buttons
          const keep = a.run && a.run(input ? input.value : undefined, card);
          if (!keep) card.remove();
        });
        row.append(b);
      }
      card.append(row);
    }
    stack.prepend(card);
    while (stack.children.length > 3) stack.lastChild.remove();
    if (opts.ms) setTimeout(() => poof(card), opts.ms);
    return card;
  }

  function agent(payload) {
    return chrome.runtime.sendMessage({ type: "agent", payload }).catch((e) => ({ ok: false, error: String(e.message || e) }));
  }

  // ---------------------------------------------------------------- live events from the service
  const SETUP = /^(telegram\.|vault\.created|vault\.locked|extension\.connected|test\.passed|rules\.saved)/;
  chrome.runtime.onMessage.addListener((msg) => {
    if (!msg || msg.type !== "walled-event" || document.visibilityState !== "visible") return;
    const e = msg.event;
    if (!SETUP.test(e.type)) return;
    show({ id: e.type, title: e.title, text: e.text, tone: e.tone, ms: e.tone === "bad" ? 12000 : 7000,
           actions: e.type === "telegram.paired" ? [{ label: "Open Walled", primary: true, run: () => { chrome.runtime.sendMessage({ type: "open-panel" }); } }] : [] });
  });

  // ---------------------------------------------------------------- one-time welcome per site
  const SITES = { "chatgpt.com": "ChatGPT", "chat.openai.com": "ChatGPT", "www.perplexity.ai": "Perplexity", "perplexity.ai": "Perplexity", "claude.ai": "Claude" };
  const site = SITES[location.hostname];
  if (site) {
    const key = `welcomed:${location.hostname}`;
    chrome.storage.local.get(key).then((v) => {
      if (v[key]) return;
      setTimeout(() => show({ id: "welcome", tone: "info", title: `Walled is watching over ${site}`,
        text: "Prompts and uploads that contain sensitive data will ask you first, in a desktop dialog. Everything else goes through instantly.",
        actions: [{ label: "Got it", primary: true, run: () => { chrome.storage.local.set({ [key]: Date.now() }); } },
                  { label: "Try it", run: () => { chrome.storage.local.set({ [key]: Date.now() }); show({ id: "try", title: "Try it", text: "Type this into the message box and press Enter:", code: "My notes WALLED_SENSITIVE", ms: 15000 }); } }] }), 1200);
    }).catch(() => {});
  }

  // ---------------------------------------------------------------- secret-copy guard
  const SECRETS = [
    [/\b\d{6,12}:AA[A-Za-z0-9_-]{33}\b/, "Telegram bot token", "telegram"],
    [/\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b/, "API key"],
    [/\bgh[pousr]_[A-Za-z0-9]{36,}\b/, "GitHub token"],
    [/\bAKIA[0-9A-Z]{16}\b/, "AWS access key"],
    [/\bxox[abpr]-[A-Za-z0-9-]{10,}\b/, "Slack token"],
    [/-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----/, "Private key"],
  ];
  let clearTimer = null;
  document.addEventListener("copy", () => {
    const text = String(window.getSelection() || "").trim();
    if (!text || text.length > 20000) return;
    const hit = SECRETS.map(([re, label, kind]) => [text.match(re), label, kind]).find(([m]) => m);
    if (!hit) return;
    const [match, label, kind] = hit;
    clearTimeout(clearTimer);
    clearTimer = setTimeout(() => navigator.clipboard.writeText("").catch(() => {}), 60_000);
    const actions = [{ label: "Keep it", run: () => { clearTimeout(clearTimer); } }];
    if (kind === "telegram") {
      actions.unshift({ label: "Connect to Walled", primary: true, run: () => {
        clearTimeout(clearTimer);
        navigator.clipboard.writeText("").catch(() => {});             // handled: no need to keep it around
        if (globalThis.WalledTelegram) { globalThis.WalledTelegram.onToken(match[0]); return false; }
        connectToken(match[0]).then((r) => r && show({ id: "pair", tone: "info", title: "Last step: press START",
          text: `Open @${r.bot} in Telegram and press START (or send the code below).`, code: r.code, steps: [2, 3],
          actions: [{ label: "Open in Telegram", primary: true, run: () => { window.open(r.link, "_blank", "noopener"); return true; } }] }));
        return false;
      } });
    }
    show({ id: "secret-copy", tone: "warn", title: `${label} copied`,
      text: "Secrets left on the clipboard can be pasted or synced somewhere by accident. Walled will clear your clipboard in 60 seconds.",
      actions, ms: 20000 });
  }, true);

  async function connectToken(token) {
    const card = show({ id: "pair", title: "Connecting your bot…", text: "Walled is checking the token with Telegram.", tone: "info" });
    const reply = await agent({ type: "pair_token", token });
    card.remove();
    if (!reply.ok) {
      show({ id: "pair", tone: "bad", title: "Couldn't connect the bot", text: reply.error || "Open the Walled app and try again.", ms: 15000 });
      return null;
    }
    return reply;
  }

  globalThis.WalledCompanion = { show, agent, connectToken };
})();
