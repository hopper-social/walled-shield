/* Pip on the desktop: the page inside the transparent always-on-top window (walled pet). */
"use strict";

(async () => {
  const bridge = () => (window.pywebview && window.pywebview.api) || null;
  await redeemTicket().catch(() => {});
  let pet = {};
  try { pet = (await api("/api/settings")).pet || {}; } catch { /* service restarting: defaults */ }

  const buddy = WalledBuddy.mount(document.body, {
    settings: pet,
    adapter: {
      resize: (mode) => (bridge() ? bridge().resize(mode) : null),
      openApp: () => (bridge() ? bridge().open_app() : api("/api/pet/open-app", { body: {} })),
      hide: () => (bridge() ? bridge().hide() : null),
      hideLabel: "Hide for now",
      sounds: "/sounds",
      save: (patch) => api("/api/settings", { method: "PUT", body: { pet: patch } }),
      introSeen: () => !!pet.intro,
      markIntro: () => api("/api/settings", { method: "PUT", body: { pet: { intro: true } } }),
      status: async () => {
        const s = await api("/api/state");
        const sites = s.guard && s.guard.extension_seen ? "Watching ChatGPT, Perplexity & Claude" : "Waiting for your browser";
        return `${sites} · Telegram ${s.telegram && s.telegram.session_active ? "on" : "off"}`;
      },
    },
  });
  window.__buddy = buddy;                        // for tests and debugging

  function handle(e) {
    if (e.type === "pet.settings") {
      const p = (e.data && e.data.pet) || {};
      if (!p.enabled || p.where === "browser") { if (bridge()) bridge().hide(); return; }
      buddy.apply(p);
      return;
    }
    buddy.react(e);
  }

  // The event stream. If the service restarted (new port/token), ask the window for a fresh link.
  let last = 0, fails = 0, primed = false;
  async function poll() {
    try {
      const r = await api(`/api/events?since=${last}`);
      fails = 0;
      if (!primed) { primed = true; last = r.last; }
      else for (const e of r.events) { last = Math.max(last, e.id); handle(e); }
    } catch {
      fails += 1;
      if (fails === 4) buddy.express("confused", 4000);           // can't reach Walled for a moment
      if (fails === 5 && bridge()) {
        try { const url = await bridge().reconnect(); location.href = url; setTimeout(() => location.reload(), 400); } catch { /* try again later */ }
      }
      if (fails > 30) fails = 0;
    }
    setTimeout(poll, 1500);
  }
  poll();
})();
