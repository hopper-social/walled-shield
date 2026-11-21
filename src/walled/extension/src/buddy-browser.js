/* Pip in the corner of ChatGPT, Perplexity and Claude, when he isn't already on the desktop.
 * Setup news stays in the companion cards (top right); Pip speaks up for what matters here:
 * something held back, Claude waiting for an answer. */
(() => {
  "use strict";
  if (window.top !== window || document.getElementById("walled-buddy")) return;
  const HOUR = 3600 * 1000;
  let buddy = null, host = null;

  async function settings() {
    let synced = null;
    try { synced = (await chrome.runtime.sendMessage({ type: "rules" })).pet; } catch { /* worker asleep */ }
    const { petLocal = {}, petHiddenUntil = 0 } = await chrome.storage.local.get(["petLocal", "petHiddenUntil"]);
    const pet = { enabled: true, in_browser: true, ...(synced || {}), ...petLocal };
    return { pet, show: pet.enabled && pet.in_browser && Date.now() > petHiddenUntil };
  }

  async function sync() {
    if (!chrome.runtime || !chrome.runtime.id) return;            // extension reloaded
    const { pet, show } = await settings();
    if (show && !buddy) mount(pet);
    else if (!show && buddy) unmount();
    else if (buddy) buddy.apply(pet);
  }

  function mount(pet) {
    host = document.createElement("div");
    host.id = "walled-buddy";
    host.style.cssText = "position:fixed;right:10px;bottom:58px;width:360px;height:450px;max-width:calc(100vw - 20px);z-index:2147483645;pointer-events:none";
    const root = host.attachShadow({ mode: "open" });    // open: nothing secret is shown here
    (document.body || document.documentElement).appendChild(host);
    buddy = WalledBuddy.mount(root, {
      settings: pet,
      filter: (e) => /^(guard\.decision|claude\.)/.test(e.type),
      adapter: {
        openApp: () => chrome.runtime.sendMessage({ type: "open-panel" }).catch(() => {}),
        hide: async () => { await chrome.storage.local.set({ petHiddenUntil: Date.now() + HOUR }); unmount(); },
        hideLabel: "Hide here for an hour",
        sounds: chrome.runtime.getURL("sounds"),
        introSeen: async () => (await chrome.storage.local.get("petIntro")).petIntro === true,
        markIntro: () => chrome.storage.local.set({ petIntro: true }),
        status: () => "Watching this chat for anything you marked sensitive.",
        save: async (patch) => {
          const { petLocal = {} } = await chrome.storage.local.get("petLocal");
          await chrome.storage.local.set({ petLocal: { ...petLocal, ...patch } });
        },
      },
    });
  }

  function unmount() { if (buddy) buddy.destroy(); if (host) host.remove(); buddy = host = null; }

  chrome.runtime.onMessage.addListener((msg) => {
    if (!msg || msg.type !== "walled-event" || !buddy || document.visibilityState !== "visible") return;
    if (msg.event.type === "pet.settings") { chrome.storage.local.remove("rulesCache").catch(() => {}); setTimeout(sync, 300); return; }
    buddy.react(msg.event);
  });
  chrome.storage.onChanged.addListener((changes, area) => { if (area === "local" && (changes.petHiddenUntil || changes.rulesCache)) sync(); });
  sync();
  const timer = setInterval(() => { if (!chrome.runtime || !chrome.runtime.id) { clearInterval(timer); unmount(); return; } sync(); }, 5 * 60 * 1000);
})();
