/*
 * Telegram Web assistant (web.telegram.org): sets up your Walled bot with you, step by step,
 * right where you already are.
 *
 *  1. Not logged in?  → "Log in with the QR code on your phone — I'll wait."
 *  2. Logged in       → asks permission: "Walled can open @BotFather and type the setup
 *                       messages for you. Nothing is sent without your click."  [Continue]
 *  3. In @BotFather   → /newbot → a name → a username (suggested, editable). Each send is one click.
 *  4. Token appears   → "Connect this bot to Walled" (sent only to the Walled app on this computer)
 *  5. Opens your bot  → you press START → the app confirms "Paired".
 *
 * It reads the page only to spot the login state, BotFather's replies and the token while you
 * are in this flow. It never reads or sends your other chats.
 */
"use strict";

(() => {
  if (globalThis.WalledTelegram) return;
  const C = globalThis.WalledCompanion;
  if (!C) return;

  const TOKEN_RE = /\b\d{6,12}:AA[A-Za-z0-9_-]{33}\b/;
  const FLOW_KEY = "walledTelegramFlow";
  let flow = null;                     // {step, name, username, startedAt}
  let lastStep = "";
  let watching = false;

  const isLoggedOut = () => !!document.querySelector("#auth-pages, .auth-pages, #auth-qr-form, .auth-form, [class*='AuthQr']");
  const isLoggedIn = () => !isLoggedOut() && !!document.querySelector("#column-left, .chatlist, .chat-list, #LeftColumn, .chatlist-container, [class*='ChatList']");
  const inBotFather = () => /botfather/i.test(location.hash) || /BotFather/.test(document.querySelector(".chat-info .peer-title, .ChatInfo .fullName, .top .peer-title, [class*='ChatInfo'] h3")?.textContent || "");

  function suggestUsername() {
    const n = crypto.getRandomValues(new Uint16Array(1))[0].toString(36).slice(0, 4);
    return `walled_${n}_bot`;
  }

  async function save(next) {
    flow = next;
    await chrome.storage.local.set({ [FLOW_KEY]: flow });
  }

  function openPeer(domain, start) {
    const addr = `tg://resolve?domain=${domain}` + (start ? `&start=${encodeURIComponent(start)}` : "");
    location.hash = "?tgaddr=" + encodeURIComponent(addr);
  }

  function composer() {
    const list = [...document.querySelectorAll(".input-message-input[contenteditable='true'], #editable-message-text, [contenteditable='true'][role='textbox'], div[contenteditable='true']")]
      .filter((el) => el.offsetParent !== null);
    return list.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top)[0] || null;
  }

  function sendText(text) {
    const box = composer();
    if (!box) {
      C.show({ id: "tg", tone: "warn", title: "Open the chat first", text: "Click into the @BotFather chat so Walled can see the message box, then press the button again.", ms: 8000 });
      return false;
    }
    box.focus();
    document.execCommand("selectAll", false);
    document.execCommand("insertText", false, text);
    box.dispatchEvent(new InputEvent("input", { bubbles: true, data: text, inputType: "insertText" }));
    const button = document.querySelector("button.btn-send, .btn-send-container button, button.send, button[aria-label='Send Message' i], button[title='Send Message' i]");
    if (button && !button.disabled) button.click();
    else box.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true }));
    return true;
  }

  function lastIncomingText() {
    const msgs = [...document.querySelectorAll(".bubble.is-in .message, .bubble:not(.is-out) .translatable-message, .Message:not(.own) .text-content, .message-list-item:not(.own) .text-content")];
    return msgs.slice(-3).map((m) => m.textContent || "").join("\n");
  }

  // ------------------------------------------------------------------ the flow
  async function render() {
    if (!flow) return;
    const step = isLoggedOut() ? "login" : flow.step;
    if (step === lastStep) return;
    lastStep = step;
    if (step === "login") {
      C.show({ id: "tg", title: "Log in to Telegram Web", steps: [0, 5],
        text: "Scan the QR code on this page with Telegram on your phone (Settings → Devices → Link Desktop Device). I'll continue as soon as you're in.", tone: "info",
        actions: [{ label: "Cancel setup", run: () => { stop(); } }] });
      return;
    }
    if (step === "consent") {
      C.show({ id: "tg", title: "You're logged in ✓", steps: [1, 5], tone: "ok",
        text: "Walled can open @BotFather (Telegram's official bot for creating bots) and type the setup messages for you. Nothing is sent without your click.",
        actions: [{ label: "Continue", primary: true, run: async () => { await save({ ...flow, step: "newbot" }); openPeer("BotFather"); rerender(); } },
                  { label: "Not now", run: () => { stop(); } }] });
      return;
    }
    if (step === "newbot") {
      C.show({ id: "tg", title: "Create your Walled bot", steps: [2, 5],
        text: "Step 1 of 3 · Ask BotFather for a new bot. (New to BotFather? /help lists everything it can do.)",
        actions: [{ label: "Send /newbot", primary: true, run: async () => { if (sendText("/newbot")) { await save({ ...flow, step: "name" }); rerender(); } return true; } },
                  { label: "Send /help", run: () => { sendText("/help"); return true; } },
                  { label: "Cancel", run: () => { stop(); } }] });
      return;
    }
    if (step === "name") {
      C.show({ id: "tg", title: "Give it a name", steps: [2, 5], input: { value: flow.name || "Walled Guard", label: "Bot name" },
        text: "Step 2 of 3 · Any name you like. People see this name in chats.",
        actions: [{ label: "Send name", primary: true, run: async (value) => { if (sendText(value || "Walled Guard")) { await save({ ...flow, name: value, step: "username", username: suggestUsername() }); rerender(); } return true; } }] });
      return;
    }
    if (step === "username") {
      C.show({ id: "tg", title: "Pick a username", steps: [3, 5], input: { value: flow.username || suggestUsername(), label: "Bot username" },
        text: "Step 3 of 3 · Must be unique and end in “bot”. If BotFather says it's taken, I'll suggest another.",
        actions: [{ label: "Send username", primary: true, run: async (value) => { if (sendText(value)) { await save({ ...flow, username: value, step: "token" }); rerender(); } return true; } }] });
      return;
    }
    if (step === "token") {
      C.show({ id: "tg", title: "Waiting for your token…", steps: [3, 5], tone: "info",
        text: "BotFather replies with a token like 123456:AA… Walled will spot it here — no copying needed.",
        actions: [{ label: "Try another username", run: async () => { await save({ ...flow, step: "username", username: suggestUsername() }); rerender(); return true; } }] });
      return;
    }
    if (step === "start") {
      C.show({ id: "tg", title: "Last step: press START", steps: [4, 5], tone: "info",
        text: `Your bot @${flow.bot} is open. Press START at the bottom of the chat — that links it to Walled.`,
        code: flow.code, actions: [{ label: "Open my bot again", run: () => { openPeer(flow.bot, flow.raw); return true; } }] });
    }
  }

  function rerender() { lastStep = ""; render(); }

  async function stop() {
    flow = null;
    lastStep = "";
    await chrome.storage.local.remove(FLOW_KEY);
  }

  async function onToken(token) {
    C.show({ id: "tg", title: "Your bot is ready 🎉", steps: [4, 5], tone: "ok",
      text: "Connect it to Walled? The token goes only to the Walled app on this computer (never to a website).",
      actions: [{ label: "Connect to Walled", primary: true, run: async () => {
        const r = await C.connectToken(token);
        if (!r) return false;
        await save({ ...(flow || {}), step: "start", bot: r.bot, code: r.code, raw: (r.code || "").replace(/^\/pair /, "") });
        openPeer(r.bot, flow.raw);
        rerender();
        return false;
      } }, { label: "Not now", run: () => {} }] });
  }

  // Watch BotFather's replies while the flow is waiting for the token / a username decision.
  const seen = new Set();
  const observer = new MutationObserver(() => {
    if (!flow || !["token", "username", "name"].includes(flow.step)) return;
    const text = lastIncomingText();
    const m = text.match(TOKEN_RE);
    if (m && !seen.has(m[0])) { seen.add(m[0]); onToken(m[0]); return; }
    if (flow.step === "token" && /already taken|is invalid|must end in/i.test(text)) {
      save({ ...flow, step: "username", username: suggestUsername() }).then(() => {
        rerender();
        C.show({ id: "tg-hint", tone: "warn", title: "That username didn't work", text: "Try the new suggestion (it has to be unique and end in “bot”).", ms: 8000 });
      });
    }
  });

  function watch() {
    if (watching) return;
    watching = true;
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    setInterval(() => {
      if (!flow) return;
      if (flow.step === "login" && isLoggedIn()) { save({ ...flow, step: "consent" }).then(rerender); return; }
      render();
    }, 1000);
  }

  // Pairing finished (event relayed by the background worker): close the flow with a success card.
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg && msg.type === "walled-event" && msg.event.type === "telegram.paired" && flow) {
      stop();
      C.show({ id: "tg", tone: "ok", title: "Paired! Walled is connected", steps: [5, 5],
        text: "Claude approvals, alerts and password-fill approvals will arrive in this chat. You can close this tab.", ms: 15000 });
    }
  });

  async function begin() {
    await save({ step: isLoggedIn() ? "consent" : "login", startedAt: Date.now() });
    watch();
    rerender();
  }

  // Started from the app / side panel ("Set up in Telegram Web"), or resume an unfinished flow.
  chrome.storage.local.get(FLOW_KEY).then((v) => {
    if (v[FLOW_KEY] && Date.now() - (v[FLOW_KEY].startedAt || 0) < 30 * 60 * 1000) {
      flow = v[FLOW_KEY];
      watch();
      setTimeout(render, 1500);
    } else if (/walled-setup/.test(location.hash + location.search)) {
      begin();
    }
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes[FLOW_KEY] && changes[FLOW_KEY].newValue && !flow) {
      flow = changes[FLOW_KEY].newValue;
      watch();
      rerender();
    }
  });

  globalThis.WalledTelegram = { begin, onToken, stop };
})();
