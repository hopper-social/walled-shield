/*
 * Background service worker: the only part of the extension that talks to the desktop agent.
 *
 * Each request opens a short-lived native-messaging connection. Chrome starts the agent
 * (`walled-guard host`), we send one message, wait for one reply, and disconnect. An open
 * native port keeps this service worker alive while a confirmation dialog is on screen.
 * Any failure resolves to {ok:false}; the content script treats that as "block".
 *
 * It also caches the user's sensitive-data rules (so prompts are checked locally, instantly)
 * and registers the optional all-sites password helper.
 */
"use strict";

const HOST = "com.walled.guard";
const TIMEOUT_MS = 150_000;          // the agent's own dialog gives up after 120 s
const DEFAULTS = { recolor: true, color: "#d9480f", passwordHelper: false };
const RULES_TTL_MS = 30_000;
const VAULT_OPS = new Set(["vault_find", "vault_fill", "vault_save", "vault_status"]);
const HELPER_ID = "walled-passwords";

let nextId = 1;

// Only requests that may wait for a person (dialog, phone, fill approval) get the long timeout.
const SLOW = new Set(["confirm_prompt", "inspect_files", "vault_fill"]);

function callAgent(payload, timeoutMs = SLOW.has(payload.type) ? TIMEOUT_MS : 15_000) {
  return new Promise((resolve) => {
    let port = null;
    let settled = false;
    const finish = (reply) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { port && port.disconnect(); } catch { /* already closed */ }
      resolve(reply);
    };
    const timer = setTimeout(() => finish({ ok: false, error: "The desktop agent did not answer in time." }), timeoutMs);
    try {
      port = chrome.runtime.connectNative(HOST);
    } catch (err) {
      finish({ ok: false, error: String((err && err.message) || err) });
      return;
    }
    port.onMessage.addListener((msg) => finish(msg && typeof msg === "object" ? msg : { ok: false, error: "bad reply" }));
    port.onDisconnect.addListener(() => {
      const reason = chrome.runtime.lastError && chrome.runtime.lastError.message;
      finish({ ok: false, error: reason || "The desktop agent closed the connection." });
    });
    port.postMessage({ ...payload, id: nextId++ });
  });
}

async function rules() {
  const { rulesCache } = await chrome.storage.local.get("rulesCache");
  if (rulesCache && Date.now() - rulesCache.at < RULES_TTL_MS) return rulesCache;
  const reply = await callAgent({ type: "ping" }, 10_000);
  if (reply.ok && Array.isArray(reply.rules)) {
    const fresh = { rules: reply.rules, at: Date.now(), telegram: !!reply.telegram, service: !!reply.service, pet: reply.pet || null };
    await chrome.storage.local.set({ rulesCache: fresh });
    return fresh;
  }
  return rulesCache || { rules: [] };
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || sender.id !== chrome.runtime.id) return false;
  if (msg.type === "rules") {
    rules().then(sendResponse);
    return true;
  }
  if (msg.type === "agent") {
    const payload = { ...msg.payload };
    if (VAULT_OPS.has(payload.type)) {
      // Never trust the page for which site a password belongs to: use the tab's real origin.
      if (!sender.tab) { sendResponse({ ok: false, error: "vault requests come from pages only" }); return false; }
      payload.origin = sender.origin || new URL(sender.url).origin;
      if (!/^https:\/\//.test(payload.origin) && !/^http:\/\/(localhost|127\.0\.0\.1)(:|$)/.test(payload.origin)) {
        sendResponse({ ok: false, error: "Walled only fills passwords on secure (https) pages." });
        return false;
      }
    }
    if (payload.type === "ping") chrome.storage.local.remove("rulesCache");
    callAgent(payload).then(sendResponse);
    return true;                     // keep the channel open for the async reply
  }
  if (msg.type === "open-panel") {
    const windowId = sender.tab && sender.tab.windowId;
    if (windowId !== undefined) chrome.sidePanel.open({ windowId }).catch(() => {});
    sendResponse({ ok: true });
    return false;
  }
  if (msg.type === "password-helper") {
    setPasswordHelper(!!msg.enabled).then(sendResponse);
    return true;
  }
  return false;
});

async function setPasswordHelper(enabled) {
  const registered = await chrome.scripting.getRegisteredContentScripts({ ids: [HELPER_ID] });
  if (enabled) {
    const granted = await chrome.permissions.contains({ origins: ["<all_urls>"] });
    if (!granted) return { ok: false, error: "Permission for all sites was not granted." };
    if (!registered.length) {
      await chrome.scripting.registerContentScripts([{ id: HELPER_ID, matches: ["<all_urls>"], js: ["src/passwords.js"],
        runAt: "document_idle", allFrames: false, persistAcrossSessions: true }]);
    }
  } else if (registered.length) {
    await chrome.scripting.unregisterContentScripts({ ids: [HELPER_ID] });
  }
  await chrome.storage.sync.set({ passwordHelper: enabled });
  return { ok: true, enabled };
}

chrome.permissions.onRemoved.addListener(() => setPasswordHelper(false));

// ---------------------------------------------------------------- live channel to the Walled service
// One long-lived native-messaging port carries events (paired, vault locked, prompt stopped, test
// passed…) from the service. We relay them to the active tab's companion and to the side panel,
// and act on "assist" events (e.g. the app asked us to start the Telegram Web setup).
let channel = null;
let retry = 2000;

async function connectChannel() {
  if (channel) return;
  const { lastEventId = 0 } = await chrome.storage.session.get("lastEventId").catch(() => ({}));
  try {
    channel = chrome.runtime.connectNative(HOST);
  } catch {
    channel = null;
    setTimeout(connectChannel, (retry = Math.min(retry * 2, 60_000)) * (0.7 + Math.random() * 0.6));
    return;
  }
  channel.onMessage.addListener(async (msg) => {
    retry = 2000;
    if (!msg || msg.type !== "event") return;
    const event = msg.event;
    chrome.storage.session.set({ lastEventId: event.id }).catch(() => {});
    if (event.type === "assist.telegram_web") return startTelegramWeb();
    if (Date.now() / 1000 - event.ts > 120) return;           // don't replay old news after a restart
    const message = { type: "walled-event", event };
    chrome.runtime.sendMessage(message).catch(() => {});      // side panel / popup, if open
    const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tab && tab.id !== undefined) chrome.tabs.sendMessage(tab.id, message).catch(() => {});
  });
  channel.onDisconnect.addListener(() => {
    channel = null;
    setTimeout(connectChannel, (retry = Math.min(retry * 2, 60_000)) * (0.7 + Math.random() * 0.6));
  });
  channel.postMessage({ type: "subscribe", since: lastEventId });
}

async function startTelegramWeb() {
  await chrome.storage.local.set({ walledTelegramFlow: { step: "login", startedAt: Date.now() } });
  const tabs = await chrome.tabs.query({ url: "https://web.telegram.org/*" });
  if (tabs.length) {
    await chrome.tabs.update(tabs[0].id, { active: true });
    await chrome.windows.update(tabs[0].windowId, { focused: true });
  } else {
    await chrome.tabs.create({ url: "https://web.telegram.org/k/" });
  }
}

chrome.runtime.onStartup.addListener(connectChannel);
connectChannel();

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.sync.get(Object.keys(DEFAULTS));
  const missing = Object.fromEntries(Object.entries(DEFAULTS).filter(([k]) => stored[k] === undefined));
  if (Object.keys(missing).length) await chrome.storage.sync.set(missing);
  if (stored.passwordHelper) setPasswordHelper(true);
  connectChannel();
});
