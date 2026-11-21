/* Walled Control Center: the views. */
"use strict";

const views = {};
let state = null;

async function refreshState() {
  state = await api("/api/state");
  const t = state.telegram;
  const line = `v${state.version} · ${t.session_active ? "Telegram on" : t.running ? "Telegram paused" : "Telegram off"}`;
  for (const id of ["foot-text", "side-status"]) { const el = document.getElementById(id); if (el) el.textContent = line; }
  document.querySelectorAll(".live-dot").forEach((d) => d.classList.toggle("on", !!t.session_active));
  return state;
}

// ====================================================================== Home — Pip, the assistant
let homePip = null, homeSay = null, topPip = null;
const onHome = () => !!(homePip && homePip.wrap.isConnected);

/** One reaction channel: Pip on the home screen, the little Pip in the top bar elsewhere. */
function pipFeel(mood, words, ms = 3200) {
  if (onHome()) { homePip.react(mood, ms); if (words) homeSay.say(words); }
  else if (topPip) topPip.react(mood, ms);
}
onEvent((e) => {
  const r = pipReaction(e);
  if (!r) return;
  pipFeel(r[0], r[1]);
  if (onHome() && /^(guard\.|claude\.)/.test(e.type) && typeof refreshRecent === "function") refreshRecent();
});
let refreshRecent = null;

function greeting(s, ins, next) {
  const hr = new Date().getHours();
  const hi = hr < 12 ? "Good morning." : hr < 17 ? "Good afternoon." : "Good evening.";
  if (next) return `${hi} 1 recommended action: ${(next.action || next.title).replace(/\.$/, "")}.`;
  if (ins.stats.stopped) return `${hi} ${ins.stats.stopped} sensitive ${ins.stats.stopped === 1 ? "send" : "sends"} intercepted this week. All controls are active.`;
  return `${hi} All controls are active on ChatGPT, Perplexity and Claude.`;
}

// ====================================================================== Overview
views.home = async () => {
  skeleton(3);
  const [s, ins, rules, recent] = await Promise.all([refreshState(), api("/api/insights"), api("/api/rules"), api("/api/activity")]);
  const t = s.telegram, g = s.guard, v = s.vault;
  const servers = Object.values(s.mcp.servers || {});
  const guarded = servers.filter((x) => x === "guarded").length;
  const todo = ins.checklist.filter((c) => !c.done);
  const next = todo[0];
  const st = ins.stats;
  const activeRules = rules.rules.filter((r) => r.enabled).length;

  if (!homePip) { homePip = new Pip({ size: 44, keep: true, mini: true }); homeSay = speech(); }
  const assist = h("div", { class: "assist" }, homePip.wrap, homeSay,
    h("button", { class: "ghost small", onclick: async () => {
      const p = await api("/api/pet/show", { body: {} });
      homePip.react("happy", 1600);
      homeSay.say(p.running ? "I'm in the corner of your screen." : p.in_browser ? "I'm keeping watch in the corner of ChatGPT, Perplexity and Claude." : "Turn me on in Settings → Companion.");
    } }, "Where's Pip?"));

  const kpi = (label, value, foot, extra) => h("div", { class: "kpi" }, h("div", { class: "kpi-label" }, label),
    h("div", { class: "kpi-value" }, extra || null, h("span", {}, value)), h("div", { class: "kpi-foot" }, foot));
  const kpis = h("div", { class: "kpis" },
    kpi("Security posture", ins.score.level, `${ins.checklist.filter((c) => c.done).length} of ${ins.checklist.length} controls in place`, scoreRing(ins.score.percent, 40)),
    kpi("Intercepted · 7 days", String(st.stopped), `${st.events} checked · ${st.warned} warned`),
    kpi("Decided on phone", String(st.by_phone), t.session_active ? "Telegram approvals on" : "Telegram approvals off"),
    kpi("Active policies", String(activeRules), `${rules.rules.length - activeRules} available to enable`));

  const status = (ic, title, ok, value, detail, href, cta) => h("a", { class: "status-row", href },
    h("span", { class: "sr-ico" }, icon(ic, 17)),
    h("span", { class: "sr-text" }, h("b", {}, title), h("small", {}, detail)),
    h("span", { class: `chip ${ok === true ? "ok" : ok === false ? "bad" : "warn"}` }, value),
    h("span", { class: "sr-cta" }, cta, icon("chev", 14)));
  const statusCard = section("Protection status", null,
    status("globe", "Browser", g.extension_seen ? true : null, g.extension_seen ? "Protected" : "Not connected",
      g.extension_seen ? `ChatGPT, Perplexity, Claude · last seen ${ago(g.extension_seen)}` : "Load the extension once", "#browser", g.extension_seen ? "Manage" : "Connect"),
    status("spark", "Claude tools", servers.length && guarded === servers.length ? true : servers.length ? null : null, servers.length ? `${guarded} of ${servers.length} guarded` : "No servers",
      servers.length ? "Tool calls wait for approval" : "Add MCP servers in Claude Desktop", "#claude", "Manage"),
    status("send", "Telegram approvals", t.session_active ? true : t.running ? null : false, t.session_active ? "On" : t.running ? "Paused" : "Not paired",
      t.session_active ? `@${t.bot || "bot"} · until ${new Date(t.session_expires_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : t.running ? "Renew to receive approvals" : "One private bot for approvals and alerts", "#telegram", t.running ? "Open" : "Pair"),
    status("key", "Password vault", v.unlocked ? true : v.exists ? null : null, v.unlocked ? "Unlocked" : v.exists ? "Locked" : "Not set up",
      v.exists ? `${v.entries ?? 0} saved · encrypted on this device` : "Strong passwords, approved fills", "#vault", v.exists ? "Open" : "Set up"));

  const actions = section("Recommended actions", todo.length ? `${todo.length} open` : null,
    ...(todo.length ? todo.slice(0, 5).map((c) => h("div", { class: "action-row" }, h("span", { class: "dot warn" }),
      h("span", { class: "grow" }, c.action || c.title), h("a", { class: "btn small", href: `#${c.view}` }, "Fix")))
      : [h("div", { class: "all-good" }, checkMark(28), h("div", {}, h("b", {}, "All controls in place"), h("small", { class: "muted" }, "Run the verification suite any time from Verification.")))]),
    todo.length > 1 ? h("button", { class: "ghost small", style: "margin-top:6px", onclick: () => onboarding(1) }, "Open guided setup") : null);

  const feed = h("div", { class: "feed" });
  const paint = (events) => feed.replaceChildren(...(events.length ? events.slice(0, 6).map(feedItem)
    : [h("div", { class: "empty" }, "No decisions yet. Intercepted prompts, uploads and Claude approvals appear here in real time.")]));
  paint(recent.events);
  refreshRecent = () => api("/api/activity").then((r) => paint(r.events));
  const activity = section("Recent activity", null, feed, h("a", { class: "btn small", href: "#activity", style: "margin-top:10px" }, "Open audit log"));

  page("Overview", { icon: "home", text: "Protection across your browser, Claude tools and phone." },
    assist, kpis, h("div", { class: "grid g2 overview" }, statusCard, actions), activity);
  homePip.set(next ? "neutral" : "happy");
  homeSay.say(greeting(s, ins, next));
};

function feedItem(e) {
  const stopped = /cancel|deny|block|expired/.test(e.decision);
  return h("div", { class: "feed-item" },
    h("span", { class: `dot ${stopped ? "bad" : e.decision === "warn" ? "warn" : "ok"}`, style: "margin-top:6px" }),
    h("div", { class: "t" }, h("b", {}, `${e.source === "claude" ? "Claude" : e.where} · ${e.what}`),
      h("small", {}, [fmtTime(e.time), e.rules && e.rules.length ? e.rules.join(", ") : "", e.via ? `via ${e.via}` : ""].filter(Boolean).join(" · "))),
    h("span", { class: `chip ${stopped ? "bad" : e.decision === "warn" ? "warn" : "ok"}` }, e.decision));
}

// ====================================================================== Sensitive data
const CATEGORIES = [
  { id: "words", icon: "word", title: "Your words & markers", text: "Exact words that are sensitive for you: client names, project codenames, the test marker.", match: (r) => r.kind === "keyword", add: "keyword", placeholder: "e.g. Acme Corp, Project Falcon" },
  { id: "ids", icon: "id", title: "Personal IDs", text: "Government and contact identifiers.", match: (r) => r.kind === "builtin" && ["aadhaar", "pan", "email", "phone_in"].includes(r.pattern) },
  { id: "money", icon: "card", title: "Money", text: "Payment cards (checked with the Luhn algorithm) and bank codes.", match: (r) => r.kind === "builtin" && ["card", "ifsc"].includes(r.pattern) },
  { id: "secrets", icon: "lock", title: "Secrets & keys", text: "API keys, tokens, private keys and written-down passwords.", match: (r) => r.kind === "builtin" && ["api_key", "private_key", "jwt", "password"].includes(r.pattern) },
  { id: "files", icon: "file", title: "Files & folders", text: "Uploads by file name (e.g. *payroll*), and folders Claude's tools shouldn't touch.", match: (r) => r.kind === "filename" || r.kind === "folder", add: "filename", placeholder: "e.g. *payroll*  or  ~/Documents/Finance" },
  { id: "patterns", icon: "code", title: "Advanced patterns", text: "Regular expressions for formats like employee IDs.", match: (r) => r.kind === "regex", add: "regex", placeholder: "e.g. \\bEMP-\\d{6}\\b" },
];
let rulesCache = null;
async function saveRules(msg) { rulesCache = await run(() => api("/api/rules", { method: "PUT", body: { rules: rulesCache.rules } }), msg); }

views.sensitive = async () => {
  skeleton(4);
  rulesCache = await api("/api/rules");
  const enabled = rulesCache.rules.filter((r) => r.enabled).length;
  const hits = h("div", { class: "hits" });
  let timer = null;
  const test = h("textarea", { placeholder: "Paste anything — a prompt, a file name like payroll_march.xlsx, or a path like ~/Documents/Finance/q1.pdf — to see what Walled would catch.",
    oninput: () => { clearTimeout(timer); timer = setTimeout(async () => {
      const r = await api("/api/rules/test", { body: { text: test.value } });
      hits.replaceChildren(...(r.hits.length ? r.hits.map((x) => h("span", { class: `chip ${x.action === "block" ? "bad" : x.action === "warn" ? "warn" : "acc"}` }, `${x.name} · ${x.sample}`))
        : [h("span", { class: "muted small" }, test.value ? "Nothing sensitive found — this would be sent without a question." : "")]));
    }, 250); } });
  const cats = h("div");
  const paint = () => cats.replaceChildren(...CATEGORIES.map(categoryCard));
  paint();
  views.sensitive.repaint = paint;
  page("Data policies", { icon: "shield", text: `${enabled} data types are monitored. Enable categories, add organisation-specific terms, and set the action for each: Ask, Warn or Block.` },
    h("div", { class: "split" },
      cats,
      h("div", { class: "sticky" },
        section("Try it", "Live check against your current rules.", test, hits),
        section("What happens when something is found", null, h("div", { class: "legend" },
          h("div", {}, h("span", { class: "chip acc" }, "Ask"), h("span", {}, "A desktop dialog asks Continue or Cancel. (Default)")),
          h("div", {}, h("span", { class: "chip warn" }, "Warn"), h("span", {}, "It goes through, you get an alert and a log entry.")),
          h("div", {}, h("span", { class: "chip bad" }, "Block"), h("span", {}, "Stopped right away, no question.")),
          h("div", {}, h("span", { class: "chip" }, "Phone"), h("span", {}, "Alert sends a masked Telegram card; Answer lets you tap Allow/Block on the phone too — first answer wins.")))))));
};

function categoryCard(cat) {
  const rules = rulesCache.rules.filter(cat.match);
  const on = rules.filter((r) => r.enabled).length;
  const body = h("div");
  for (const r of rules) body.append(ruleRow(r));
  let add = null;
  if (cat.add) {
    const input = h("input", { type: "text", placeholder: cat.placeholder, "aria-label": `Add to ${cat.title}` });
    add = h("form", { class: "add-inline", onsubmit: async (e) => {
      e.preventDefault();
      const value = input.value.trim();
      if (!value) return;
      const kind = cat.add === "filename" && /[\\/~]/.test(value) && !value.includes("*") ? "folder" : cat.add;
      rulesCache.rules.push({ name: kind === "folder" ? `Folder: ${value}` : kind === "filename" ? `Files: ${value}` : value, kind, pattern: value,
        case_sensitive: false, action: "ask", phone: "notify", browser: kind !== "folder", mcp: true, enabled: true });
      try { await saveRules("Added"); views.sensitive.repaint(); } catch { rulesCache.rules.pop(); }
    } }, input, h("button", { class: "primary", type: "submit" }, icon("plus", 16), "Add"));
  }
  return h("section", { class: "card" },
    h("div", { class: "cat-head" }, h("div", { class: "cat-icon" }, icon(cat.icon)), h("div", {}, h("h2", {}, cat.title), h("p", {}, cat.text)),
      h("span", { class: `chip cat-count ${on ? "ok" : ""}` }, rules.length ? `${on} of ${rules.length} on` : "none yet")),
    body, add);
}

function ruleRow(r) {
  const det = r.kind === "builtin" ? rulesCache.detectors[r.pattern] : null;
  const update = (patch, msg) => { Object.assign(r, patch); saveRules(msg); };
  const more = h("div", { class: "rule-more", hidden: true },
    h("label", {}, "When found", seg(r.action, [["ask", "Ask"], ["warn", "Warn"], ["block", "Block"]], (v) => update({ action: v }, "Saved"))),
    h("label", {}, "Telegram", seg(r.phone, [["off", "Off"], ["notify", "Alert"], ["approve", "Answer"]], (v) => update({ phone: v }, "Saved"))),
    h("label", {}, "Applies to", h("div", { class: "row" },
      h("span", { class: "row small" }, sw(r.browser, (v) => update({ browser: v }, "Saved"), "Web"), "Web chats"),
      h("span", { class: "row small" }, sw(r.mcp, (v) => update({ mcp: v }, "Saved"), "Claude"), "Claude tools"))),
    r.id === "marker" || r.kind === "builtin" ? null : h("button", { class: "ghost danger", onclick: () => {
      rulesCache.rules = rulesCache.rules.filter((x) => x !== r); saveRules("Removed").then(() => views.sensitive.repaint()); } }, "Remove"));
  const summary = { ask: "asks you", warn: "warns", block: "blocks" }[r.action] + (r.phone === "approve" ? " · phone can answer" : r.phone === "notify" ? " · Telegram alert" : "");
  return h("div", { class: "rule-row" },
    sw(r.enabled, (v) => update({ enabled: v }, v ? `${r.name}: on` : `${r.name}: off`), `Enable ${r.name}`),
    h("div", { class: "name" }, h("b", {}, r.name), h("small", {}, det ? h("code", {}, det.example) : r.kind === "keyword" ? "exact words" : h("code", {}, r.pattern), " · ", summary)),
    h("button", { class: "ghost small", onclick: (e) => { more.hidden = !more.hidden; e.currentTarget.textContent = more.hidden ? "Options" : "Done"; } }, "Options"),
    more);
}

// ====================================================================== Browser
views.browser = async () => {
  skeleton(2);
  const [s, b] = await Promise.all([refreshState(), api("/api/browser")]);
  const g = s.guard;
  const statusRow = (ok, text, sub) => h("div", { class: "list-item" }, h("span", { class: `dot ${ok === null ? "" : ok ? "ok" : "warn"}` }),
    h("div", { class: "grow" }, h("b", {}, text), sub ? h("span", { class: "muted small" }, sub) : null));
  page("Browser", { icon: "globe", text: "Walled checks prompts and uploads on ChatGPT, Perplexity and Claude before they leave the browser, and recolours AI answers so you can tell them apart." },
    h("div", { class: "grid g2" },
      section("Status", "Walled repairs its own browser connection when it starts.",
        statusRow(!!b.extension_seen, b.extension_seen ? "Extension connected" : "Extension not seen yet", b.extension_seen ? `Last seen ${ago(b.extension_seen)}` : "Load it once (steps on the right), then open ChatGPT."),
        statusRow(g.browsers.length > 0, g.browsers.length ? `Desktop agent ready in ${g.browsers.join(", ")}` : "No Chromium browser found", "Reads files and shows the confirmation dialog."),
        statusRow(g.ocr, g.ocr ? "Reads text in screenshots and scans" : "Screenshots can't be read yet", g.ocr ? "OCR engine installed." : "Install Tesseract (macOS: brew install tesseract). Until then image uploads always ask."),
        statusRow(!String(g.dialog).startsWith("NONE"), "Desktop dialog", String(g.dialog)),
        h("div", { class: "row", style: "margin-top:12px" }, h("button", { class: "primary", onclick: () => run(() => api("/api/tests/dialog", { body: {} })).then((r) => toast(`Dialog works — you chose ${r.choice}`)) }, "Show a test dialog"),
          h("a", { class: "btn", href: "#tests" }, "More tests"))),
      section("Load the extension (one time)", "About 20 seconds.",
        extensionSteps(b.extension_path),
        h("p", { class: "muted small" }, "Then pin Walled to the toolbar → “Open Walled in the side panel”. After an update, press ↻ on the extension card."),
        h("p", { class: "muted small" }, "Extension ID: ", h("code", {}, g.extension_id)))));
};

// ====================================================================== Claude tools
views.claude = async () => {
  skeleton(2);
  const c = await api("/api/connectors");
  const entries = Object.entries(c.servers || {});
  const act = async (action, names) => {
    const r = await run(() => api(`/api/connectors/${action}`, { body: { names } }));
    if (r.note) toast(r.note);
    views.claude();
  };
  page("Claude tools", { icon: "spark", text: "Every guarded Claude Desktop tool (MCP server) waits for Allow on your phone before it runs. Your sensitive-data rules also scan its inputs.",
    actions: entries.some(([, s]) => s === "UNGUARDED") ? h("button", { class: "primary", onclick: () => act("wrap", null) }, "Guard all") : null },
    c.error ? h("div", { class: "notice" }, c.error) : null,
    h("div", { class: "split" },
      section("Claude Desktop servers", `From ${c.path}`,
        entries.length ? entries.map(([name, st]) => h("div", { class: "list-item" },
          h("span", { class: `dot ${st === "guarded" ? "ok" : st === "UNGUARDED" ? "bad" : ""}` }),
          h("div", { class: "grow" }, h("b", {}, name), h("span", { class: "muted small" }, st === "guarded" ? "Asks you on Telegram before each tool call" : st === "UNGUARDED" ? "Runs without asking" : st)),
          st === "guarded" ? h("button", { onclick: () => act("unwrap", [name]) }, "Stop guarding")
            : st === "UNGUARDED" ? h("button", { class: "primary", onclick: () => act("wrap", [name]) }, "Guard") : null))
          : h("div", { class: "empty" }, "No MCP servers in Claude Desktop yet. Add one in Claude → Settings → Developer, then come back.")),
      h("div", {}, section("How it works", null, h("div", { class: "legend" },
        h("div", {}, icon("phone", 16), h("span", {}, "Claude wants to use a tool → a card arrives on Telegram with Allow / Block.")),
        h("div", {}, icon("shield", 16), h("span", {}, "If the request contains sensitive data, the card hides its inputs and “Always allow” is not offered.")),
        h("div", {}, icon("lock", 16), h("span", {}, "No answer, Telegram off or Walled stopped → the tool doesn't run.")))),
        h("div", { class: "explain" }, icon("bolt", 16), h("span", {}, "After guarding, quit and reopen Claude Desktop so it picks up the change.")))));
};

// ====================================================================== Telegram
views.telegram = async () => {
  skeleton(2);
  const s = await refreshState(), t = s.telegram;
  const body = [];
  if (t.configured || t.running) {
    const renewBox = h("div");
    const remaining = t.session_expires_at ? Math.max(0, t.session_expires_at - Date.now() / 1000) : 0;
    const testOut = h("div", { class: "muted small" });
    body.push(h("div", { class: "grid g2" },
      h("section", { class: "card" },
        h("div", { class: "bot-row" }, h("div", { class: `avatar ${t.session_active ? "" : "dim"}` }, "🤖"),
          h("div", { style: "flex:1" }, h("h2", { style: "margin:0;font-size:17px" }, t.bot ? `@${t.bot}` : "Your bot"),
            h("div", { class: "row" }, h("span", { class: `chip ${t.session_active ? "ok" : t.running ? "warn" : "bad"}` },
              t.session_active ? "Approvals on" : t.running ? "Approvals paused" : "Offline"),
              t.session_active ? h("span", { class: "muted small" }, `${Math.floor(remaining / 3600)} h ${Math.floor(remaining % 3600 / 60)} min left`) : null))),
        t.session_active ? h("div", { class: "session-bar" }, h("i", { style: `width:${Math.round(100 * remaining / 86400)}%` })) : null,
        t.error ? h("div", { class: "notice" }, t.error) : null,
        h("div", { class: "row", style: "margin-top:12px" },
          h("button", { class: "primary", onclick: () => renewFlow(renewBox, { onDone: () => views.telegram() }) }, t.session_active ? "Extend to 24 h" : "Turn on for 24 h"),
          h("button", { onclick: async () => { testOut.textContent = "Check your phone…"; const r = await run(() => api("/api/tests/telegram", { body: {} })); testOut.textContent = r.ok ? `✓ Works — you tapped ${r.choice === "allow" ? "Allow" : "Block"}.` : `No answer (${r.reason}).`; } }, "Test Allow / Block")),
        testOut, renewBox,
        h("p", { class: "muted small" }, "For safety, approvals pause after 24 hours or when you send /pairout. Turning them on needs the code shown here.")),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", {}, "Activity log channel"),
          h("p", { class: "muted small" }, "A separate group or channel that receives a quiet, masked feed of everything Walled decides — so your approvals chat stays clean.")),
        t.activity_channel ? h("div", { class: "notice ok" }, "✓ Connected. Every decision is mirrored there (rule names and outcomes only).") : h("div", { id: "activity-box" },
          h("button", { class: "primary", onclick: (e) => activityFlow(e.currentTarget.parentElement, { onDone: () => views.telegram() }) }, "Connect a log channel")))));
    if (sessionStorage.getItem("walled-renew")) { sessionStorage.removeItem("walled-renew"); setTimeout(() => renewFlow(renewBox, { onDone: () => views.telegram() }), 50); }
    body.push(section("What arrives on Telegram", "Prompt text, files and passwords never go to Telegram.",
      h("div", { class: "tg-preview" },
        h("div", { class: "bubble" }, h("b", {}, "🔐 Claude wants to run an MCP action"), h("div", {}, "Tool: send_email · Server: mail"), h("div", { class: "kb" }, h("span", {}, "Block"), h("span", {}, "Allow"))),
        h("div", { class: "bubble" }, h("b", {}, "🛡 Sensitive data on ChatGPT"), h("div", {}, "• Payment card number — •••• 1111"), h("div", { class: "kb" }, h("span", {}, "Block"), h("span", {}, "Allow"))),
        h("div", { class: "bubble" }, h("b", {}, "🔑 Password fill requested"), h("div", {}, "github.com · vi•••@example.com"), h("div", { class: "kb" }, h("span", {}, "Block"), h("span", {}, "Allow")))),
      h("p", { class: "muted small" }, "Commands: /status /guard /vault /lock /rules /clear /pairout /help")));
    const again = h("div");
    body.push(h("details", { class: "card", ontoggle: (e) => { if (e.target.open && !again.childNodes.length) pairingFlow(again, { onDone: () => views.telegram() }); } },
      h("summary", {}, "Pair a different bot"), again));
  } else {
    const box = h("section", { class: "card" });
    body.push(box);
    setTimeout(() => pairingFlow(box, { onDone: () => views.telegram() }), 0);
  }
  page("Telegram", { icon: "send", text: "One private bot for Claude approvals, sensitive-data alerts and password-fill approvals." }, ...body);
};

// ====================================================================== Passwords
views.vault = async () => {
  skeleton(2);
  const v = await api("/api/vault");
  if (!v.exists) return vaultCreate(v);
  if (!v.unlocked) return vaultUnlock(v);
  const { entries } = await api("/api/vault/entries");
  const search = h("input", { type: "text", placeholder: "Search sites or usernames", style: "flex:1", "aria-label": "Search" });
  const body = h("div");
  const render = () => {
    const q = search.value.toLowerCase();
    const rows = entries.filter((e) => !q || e.origin.includes(q) || e.username.toLowerCase().includes(q));
    body.replaceChildren(...(rows.length ? rows.map(entryRow) : [h("div", { class: "empty" }, entries.length ? "No match." : "No passwords yet. Use the practice sign-up page in the Test center, or add one here.")]));
  };
  search.addEventListener("input", render);
  render();
  page("Passwords", { icon: "key", text: `Encrypted on this computer${v.mode === "keychain" ? `, unlocked with ${v.keychain_label}` : " with your vault passphrase"}. The browser helper suggests strong passwords and fills logins you approve.`,
    actions: [h("button", { class: "primary", onclick: () => entryForm() }, icon("plus", 16), "Add"), h("button", { onclick: () => run(() => api("/api/vault/lock", { body: {} }), "Vault locked").then(() => views.vault()) }, icon("lock", 16), "Lock")] },
    h("div", { class: "split" },
      section(`${entries.length} saved logins`, null, h("div", { class: "row" }, search), body),
      h("div", { class: "sticky" }, generatorCard(),
        section("Use it in the browser", null, h("p", { class: "muted small", style: "margin-top:0" }, "Turn on “Suggest & fill passwords” in the Walled extension popup. Then try it:"),
          h("div", { class: "row" }, h("a", { class: "btn", href: "/practice/signup", target: "_blank" }, "Practice sign-up"), h("a", { class: "btn", href: "/practice/login", target: "_blank" }, "Practice login"))))));
};
function entryRow(e) {
  const pw = h("span", { class: "pw muted" }, "••••••••");
  return h("div", { class: "list-item" },
    h("div", { class: "cat-icon", style: "width:32px;height:32px" }, e.origin.slice(0, 1).toUpperCase()),
    h("div", { class: "grow" }, h("b", {}, e.origin), h("span", { class: "muted small" }, e.username || "no username")),
    pw,
    h("button", { class: "ghost", onclick: async () => { const r = await run(() => api(`/api/vault/entries/${e.id}/password`)); pw.textContent = r.password; pw.classList.remove("muted"); setTimeout(() => { pw.textContent = "••••••••"; pw.classList.add("muted"); }, 15000); } }, "Show"),
    h("button", { class: "ghost", onclick: async () => { const r = await run(() => api(`/api/vault/entries/${e.id}/password`)); await navigator.clipboard.writeText(r.password); toast("Copied — clipboard clears in 30 s"); setTimeout(() => navigator.clipboard.writeText(""), 30000); } }, "Copy"),
    h("button", { class: "ghost", onclick: () => entryForm(e) }, "Edit"),
    h("button", { class: "ghost danger", onclick: async () => { if (confirm(`Delete the ${e.origin} login?`)) { await run(() => api(`/api/vault/entries/${e.id}`, { method: "DELETE" }), "Deleted"); views.vault(); } } }, "Delete"));
}
function meter(score) { return h("div", { class: "meter", title: `Strength ${score}/4` }, h("i", { style: `width:${score * 25}%` })); }
function generatorCard() {
  const out = h("div", { class: "code-big pw" }, "—");
  const len = h("input", { type: "number", min: 12, max: 64, value: 20, style: "width:80px", "aria-label": "Length" });
  const sym = h("input", { type: "checkbox", checked: true, "aria-label": "Symbols" });
  const m = h("div");
  const gen = async () => { const r = await api("/api/vault/generate", { body: { length: +len.value, symbols: sym.checked } }); out.textContent = r.password; m.replaceChildren(meter(r.strength)); };
  gen();
  return section("Password generator", null, out, m,
    h("div", { class: "row", style: "margin-top:10px" }, h("label", { class: "row small" }, "Length", len), h("label", { class: "row small" }, sym, "Symbols"),
      h("button", { onclick: gen }, "New"), copyBtn(() => out.textContent)));
}
function entryForm(e = {}) {
  const origin = h("input", { type: "text", value: e.origin || "", placeholder: "github.com" });
  const user = h("input", { type: "text", value: e.username || "", placeholder: "you@example.com", autocomplete: "off" });
  const pass = secretInput({ placeholder: e.id ? "Leave empty to keep the current password" : "Password" });
  const note = h("input", { type: "text", value: e.note || "" });
  page(e.id ? "Edit login" : "Add login", { icon: "key", text: "Saved encrypted in your vault." },
    h("section", { class: "card stack", style: "max-width:560px" },
      h("label", { class: "field" }, "Site", origin), h("label", { class: "field" }, "Username", user),
      h("label", { class: "field" }, "Password", h("div", { class: "row" }, pass, h("button", { onclick: async () => { pass.input.value = (await api("/api/vault/generate", { body: {} })).password; pass.input.classList.add("shown"); } }, "Generate"))),
      h("label", { class: "field" }, "Note", note),
      h("div", { class: "row" }, h("button", { class: "primary", onclick: async () => {
        let password = pass.input.value;
        if (e.id && !password) password = (await api(`/api/vault/entries/${e.id}/password`)).password;
        await run(() => api("/api/vault/entries", { body: { id: e.id, origin: origin.value, username: user.value, password, note: note.value } }), "Saved");
        location.hash = "#vault"; views.vault();
      } }, "Save"), h("button", { onclick: () => views.vault() }, "Cancel"))));
}

/** Creating the vault: two clear choices, and the passphrase is explained in plain words. */
function vaultChoices(v, onDone) {
  const box = h("div", { class: "stack" });
  const choose = () => box.replaceChildren(
    h("div", { class: "grid g2" },
      v.keychain_available ? h("button", { class: "method", onclick: async () => {
        await run(() => api("/api/vault/create", { body: { mode: "keychain" } }), "Vault created");
        onDone();
      } }, h("span", { class: "chip acc rec" }, "Recommended"), h("b", {}, "Unlock with this Mac"),
        h("small", {}, `Walled creates a strong random key and keeps it in ${v.keychain_label}. Nothing to remember. macOS may ask for your Mac login password to let Walled use it.`)) : null,
      h("button", { class: "method", onclick: passphraseForm }, h("span", { class: "chip rec" }, "Works everywhere"), h("b", {}, "Choose a vault passphrase"),
        h("small", {}, "A new passphrase only for your Walled vault. You type it when you unlock."))));
  const passphraseForm = () => {
    const a = secretInput({ placeholder: "Vault passphrase", "aria-label": "Vault passphrase" });
    const b = secretInput({ placeholder: "Type it again", "aria-label": "Repeat vault passphrase" });
    const ok = h("input", { type: "checkbox", "aria-label": "I have saved it" });
    const m = h("div", { class: "meter" }, h("i"));
    a.input.addEventListener("input", () => { m.firstChild.style.width = `${Math.min(100, a.input.value.length * 5)}%`; });
    box.replaceChildren(h("div", { class: "explain" }, icon("key", 16), h("span", {}, h("b", {}, "This is not your Mac password. "),
        "It's a new passphrase that only unlocks your Walled vault. Walled never stores or sends it, so it can't be recovered — write it down or keep it in a safe place.")),
      h("div", { class: "row" }, a, h("button", { onclick: () => { const p = passphrase(); a.input.value = b.input.value = p; a.input.classList.add("shown"); b.input.classList.add("shown"); m.firstChild.style.width = "100%"; } }, "Suggest one")),
      m, b, h("label", { class: "row small" }, ok, "I've written it down / saved it somewhere safe"),
      h("div", { class: "row" }, h("button", { class: "ghost", onclick: choose }, "← Back"),
        h("button", { class: "primary", onclick: async () => {
          if (a.input.value.length < 10) return toast("Use at least 10 characters (the suggestion is a good start).", true);
          if (a.input.value !== b.input.value) return toast("The two passphrases don't match.", true);
          if (!ok.checked) return toast("Tick the box once you've saved it — it can't be recovered.", true);
          await run(() => api("/api/vault/create", { body: { master: a.input.value } }), "Vault created");
          onDone();
        } }, "Create vault")));
  };
  choose();
  return box;
}
function vaultCreate(v) {
  page("Passwords", { icon: "key", text: "Create your encrypted vault. It stays on this computer; the browser helper uses it to suggest strong passwords and fill logins you approve." },
    h("section", { class: "card", style: "max-width:760px" }, vaultChoices(v, () => views.vault())));
}
function vaultUnlock(v) {
  if (v.mode === "keychain") {
    page("Passwords", { icon: "key", text: "Your vault is locked. It locks itself after a while, and you can lock it from Telegram with /lock." },
      h("section", { class: "card stack", style: "max-width:460px" }, h("p", { style: "margin:0" }, `The key is in ${v.keychain_label}. macOS may ask for your Mac login password.`),
        h("button", { class: "primary big", onclick: async () => { await run(() => api("/api/vault/unlock", { body: { mode: "keychain" } }), "Unlocked"); views.vault(); } }, "Unlock with this Mac")));
    return;
  }
  const a = secretInput({ placeholder: "Vault passphrase", "aria-label": "Vault passphrase" });
  const go = async () => { await run(() => api("/api/vault/unlock", { body: { master: a.input.value } }), "Unlocked"); views.vault(); };
  a.input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  page("Passwords", { icon: "key", text: "Your vault is locked. It locks itself after a while, and you can lock it from Telegram with /lock." },
    h("section", { class: "card stack", style: "max-width:460px" },
      h("label", { class: "field" }, "Vault passphrase", a, h("span", {}, "The passphrase you chose when you created the vault — not your computer password.")),
      h("button", { class: "primary", onclick: go }, "Unlock")));
  a.input.focus();
}

// ====================================================================== Test center
const TEST_INFO = {
  dialog: { icon: "eye", text: "Shows the real desktop confirmation dialog.", run: "dialog" },
  telegram: { icon: "phone", text: "Sends a test card to your bot. Tap Allow or Block.", run: "telegram" },
  race: { icon: "bolt", text: "Same question on the computer and the phone — the first answer wins, the other disappears.", run: "race" },
  prompt_chatgpt: { icon: "word", text: "In ChatGPT type “My notes WALLED_SENSITIVE” and press Enter.", link: ["Open ChatGPT", "https://chatgpt.com"] },
  prompt_perplexity: { icon: "word", text: "Same on Perplexity.", link: ["Open Perplexity", "https://www.perplexity.ai"] },
  prompt_claude: { icon: "word", text: "Same on Claude.ai.", link: ["Open Claude", "https://claude.ai"] },
  file_pdf: { icon: "file", text: "Download the sample PDF and upload it to any of the chats.", file: "secret_report.pdf" },
  file_image: { icon: "file", text: "Upload the sample screenshot: Walled reads the text in the picture.", file: "secret_screenshot.png" },
  detector_card: { icon: "card", text: "Paste the test card number 4111 1111 1111 1111 into a chat.", copy: "Card 4111 1111 1111 1111" },
  password_save: { icon: "key", text: "Turn on the password helper, then use a suggested password on the practice sign-up page and save it.", link: ["Practice sign-up", "/practice/signup"] },
  password_fill: { icon: "key", text: "Fill the saved login on the practice login page.", link: ["Practice login", "/practice/login"] },
  mcp: { icon: "spark", text: "Guard a Claude Desktop server, ask Claude to use it, and tap Allow on Telegram.", link: ["Claude tools", "#claude"] },
};
views.tests = async () => {
  skeleton(3);
  const { tests } = await api("/api/tests");
  const ids = Object.keys(tests);
  const passed = ids.filter((k) => tests[k].ok).length;
  const grid = h("div", { class: "grid g3" }, ids.map((id) => testCard(id, tests[id])));
  onEvent((e) => { if (e.type === "test.passed" && grid.isConnected) views.tests(); });
  page("Verification", { icon: "flask", text: "End-to-end checks that pass only when the control actually worked. Results update live as you exercise them in the browser." },
    h("section", { class: "card" }, h("div", { class: "row spread" }, h("b", {}, `${passed} of ${ids.length} verified`), h("span", { class: "muted small" }, "Tip: open Walled in the Chrome side panel and keep this page next to the chat you're testing.")),
      h("div", { class: "progress", style: "margin-top:10px" }, h("i", { style: `width:${Math.round(100 * passed / ids.length)}%` }))),
    grid);
};
function testCard(id, t) {
  const info = TEST_INFO[id] || { icon: "flask", text: "" };
  const out = h("div", { class: "muted small" });
  const actions = h("div", { class: "row" });
  if (info.run) actions.append(h("button", { class: "primary", onclick: async () => {
    out.textContent = info.run === "dialog" ? "Look for the dialog…" : "Check your phone" + (info.run === "race" ? " and the computer…" : "…");
    try { const r = await api(`/api/tests/${info.run}`, { body: {} }); out.textContent = r.ok ? `✓ ${r.via ? `answered on the ${r.via === "phone" ? "phone" : "computer"} first` : `you chose ${r.choice}`}` : `Not passed: ${r.reason}`; }
    catch (e) { out.textContent = e.message; }
  } }, "Run test"));
  if (info.link) actions.append(h("a", { class: "btn", href: info.link[1], target: info.link[1].startsWith("#") ? null : "_blank", rel: "noopener" }, info.link[0]));
  if (info.file) actions.append(h("a", { class: "btn", href: `/samples/${info.file}`, download: info.file }, "Download sample"));
  if (info.copy) actions.append(copyBtn(info.copy, "Copy test text"));
  return h("div", { class: `card test ${t.ok ? "passed" : ""}` },
    h("span", { class: `chip status ${t.ok ? "ok" : ""}` }, t.ok ? "✓ Passed" : "Not yet"),
    h("div", { class: "cat-icon" }, icon(info.icon)), h("h3", {}, t.title), h("p", {}, info.text),
    t.ok && t.detail ? h("div", { class: "muted small" }, `${t.detail} · ${new Date(t.at * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}`) : null,
    actions, out);
}

// ====================================================================== Activity
views.activity = async () => {
  skeleton(2);
  const [r, s] = await Promise.all([api("/api/activity"), refreshState()]);
  let filter = "all";
  const list = h("div");
  const paint = (events) => {
    const rows = events.filter((e) => filter === "all" || (filter === "stopped" ? /cancel|deny|block/.test(e.decision) : e.source === filter));
    list.replaceChildren(...(rows.length ? rows.map(feedItem) : [h("div", { class: "empty" }, "Nothing here yet.")]));
  };
  paint(r.events);
  onEvent((e) => { if (e.type === "guard.decision" && list.isConnected) api("/api/activity").then((x) => { r.events = x.events; paint(x.events); }); });
  page("Audit log", { icon: "pulse", text: "Every decision, in real time. Only rule names, sites and outcomes are recorded — never prompt text, file contents or tool inputs." },
    s.telegram.running && !s.telegram.activity_channel ? h("div", { class: "notice info row spread" }, h("span", {}, "Mirror this feed to a Telegram group or channel?"), h("a", { class: "btn", href: "#telegram" }, "Connect log channel")) : null,
    h("section", { class: "card" }, h("div", { class: "row spread", style: "margin-bottom:6px" },
      seg("all", [["all", "All"], ["browser", "Browser"], ["claude", "Claude"], ["stopped", "Stopped"]], (v) => { filter = v; paint(r.events); }, "Filter"),
      h("div", { class: "row" }, s.telegram.activity_channel ? h("span", { class: "chip ok" }, "Mirrored to Telegram") : null,
        h("button", { class: "ghost", onclick: () => exportAudit("csv") }, icon("file", 15), "Export CSV"),
        h("button", { class: "ghost", onclick: () => exportAudit("json") }, icon("code", 15), "Export JSON"))), list));
};

async function exportAudit(format) {
  const res = await fetch(`/api/activity/export?format=${format}`, { headers: { "X-Walled-Token": TOKEN } });
  if (!res.ok) return toast("Export failed", true);
  const url = URL.createObjectURL(await res.blob());
  const a = h("a", { href: url, download: `walled-audit-${new Date().toISOString().slice(0, 10)}.${format}` });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast("Audit log exported");
}

// ====================================================================== Settings
views.settings = async () => {
  skeleton(2);
  const s = await api("/api/settings");
  const save = (patch) => run(() => api("/api/settings", { method: "PUT", body: patch }), "Saved");
  const num = (key, min, max, unit) => h("div", { class: "row" }, h("input", { type: "number", min, max, value: s[key], style: "width:84px", "aria-label": key, onchange: (e) => save({ [key]: +e.target.value }) }), h("span", { class: "muted small" }, unit));
  const setting = (ic, title, text, eg, control, wide) => h("div", { class: "setting" }, h("div", { class: "s-icon" }, icon(ic)),
    h("div", { style: wide ? "grid-column: 2 / -1" : "" }, h("h3", {}, title), h("p", {}, text), eg ? h("div", { class: "eg" }, eg) : null,
      wide ? h("div", { style: "margin-top:10px" }, control) : null), wide ? null : control);
  page("Settings", { icon: "gear", text: "Each setting in plain words, with what it means day to day." },
    h("div", { class: "grid g2" },
      section("Questions & answers", null,
        setting("clock", "How long a question waits", "If nobody answers the desktop dialog or the Telegram card in time, Walled treats it as Cancel / Block. Safe by default.", "Example: 120 s — a forgotten dialog never sends anything.", num("dialog_timeout", 15, 600, "seconds")),
        setting("bell", "Telegram alerts for the browser", "Rules set to Alert or Answer send a masked card to your bot (e.g. “Payment card •••• 1111”).", null, sw(s.guard_alerts, (v) => save({ guard_alerts: v }))),
        setting("pulse", "Mirror activity to the log channel", "Every decision is also posted quietly to your Telegram activity group/channel, if you connected one.", null, sw(s.activity_channel, (v) => save({ activity_channel: v })))),
      section("Passwords", null,
        setting("lock", "Auto-lock the vault", "After this many minutes without use, the vault locks itself. You can also send /lock to your bot.", "Shorter is safer; 15 min is a good balance.", num("vault_autolock_minutes", 1, 240, "minutes")),
        setting("phone", "Confirm before filling a password", "A second check before the vault fills anything — so no other program on this computer can quietly get a password.", null,
          seg(s.fill_approval, [["phone", "Telegram tap", "Dialog if Telegram is off"], ["desktop", "Desktop dialog"], ["none", "No check"]], (v) => save({ fill_approval: v }), "Fill confirmation"), true)),
      section("Claude tools", null,
        setting("spark", "Scan Claude's tool inputs", "Your sensitive-data rules also check what Claude sends to tools. A match hides the inputs on Telegram and turns off “Always allow” for that call.", null, sw(s.mcp_rules, (v) => save({ mcp_rules: v })))),
      pipSettings(s.pet || {}, save),
      section("Setup", null,
        setting("flask", "Guided setup", "Run the guided setup again, with its built-in verification at each step.", null, h("button", { onclick: () => onboarding(0) }, "Run setup")))));
};

// ---------------------------------------------------------------------- Pip settings
let previewSound = null;
function pipSettings(pet, save) {
  const savePet = (patch) => save({ pet: patch }).then((r) => { Object.assign(pet, r.pet); paintStatus(); return r; });
  const sound = () => { previewSound = previewSound || new WalledBuddy.Sound(); previewSound.set(pet); previewSound.unlock(); return previewSound; };
  const status = h("div", { class: "muted small", role: "status" });
  const showBtn = h("button", { class: "primary", onclick: async () => { const r = await run(() => api("/api/pet/show", { body: {} })); Object.assign(pet, r.settings); paintStatus(r); } }, "Show Pip now");
  const hideBtn = h("button", { onclick: async () => paintStatus(await run(() => api("/api/pet/hide", { body: {} }))) }, "Hide for now");
  async function paintStatus(r) {
    r = r || await api("/api/pet").catch(() => null);
    if (!r) return;
    status.textContent = r.running ? "On your screen now (corner of the desktop)." : r.in_browser ? "Living in the corner of ChatGPT, Perplexity and Claude." :
      !r.settings.enabled ? "Off." : r.available ? "Not on screen right now." : "Desktop mode needs pywebview, so Pip lives in the browser corner.";
    hideBtn.hidden = !r.running;
  }
  paintStatus();
  const pet_ = (key, options, label) => seg(pet[key], options, (v) => savePet({ [key]: v }), label);
  const setting = (ic, title, text, control) => h("div", { class: "setting" }, h("div", { class: "s-icon" }, icon(ic)),
    h("div", { style: "grid-column: 2 / -1" }, h("h3", {}, title), h("p", {}, text), h("div", { style: "margin-top:10px" }, control)));
  const volume = h("input", { type: "range", min: 0, max: 100, value: pet.volume, "aria-label": "Pip's volume", style: "width:220px;accent-color:var(--signal)",
    onchange: (e) => savePet({ volume: +e.target.value }).then(() => sound().play("boop")) });
  return h("section", { class: "card", style: "grid-column: 1 / -1" },
    h("div", { class: "card-head pip-settings-head" }, h("span", { class: "pip-face-lg" }, pipFace(40)),
      h("div", {}, h("h2", {}, "Companion (Pip)"),
        h("p", { class: "muted small" }, "Pip lives quietly in a corner: stands guard, paces, plays and sometimes vanishes for a while. Pet him with the pointer, boop him with a click, right-click for his menu. When something matters he hops up with a clear note and a soft sound."))),
    h("div", { class: "grid g2" },
      h("div", {},
        setting("eye", "Show Pip", "Turn him off and he stays out of sight; Walled still protects you.", h("div", { class: "row" }, sw(pet.enabled, (v) => savePet({ enabled: v })), showBtn, hideBtn, status)),
        setting("globe", "Where he lives", "Screen corner keeps him with you everywhere. Browser corner shows him only on ChatGPT, Perplexity and Claude.",
          pet_("where", [["auto", "Wherever fits"], ["desktop", "Screen corner"], ["browser", "Browser corner"], ["both", "Both"]], "Where Pip lives")),
        setting("home", "Which corner", "Bottom right stays clear of most apps.", pet_("corner", [["bottom-right", "Bottom right"], ["bottom-left", "Bottom left"]], "Corner"))),
      h("div", {},
        setting("spark", "His mood", "Calm moves now and then. Playful plays more. Focus mostly stands guard and stays quiet.",
          pet_("mood", [["calm", "Calm"], ["playful", "Playful"], ["focus", "Focus"]], "Mood")),
        setting("bell", "When you pet him", "Pick what feels nice. Alerts are always soft, warm tones: noticeable, never alarming.",
          h("div", { class: "stack-sm" }, pet_("sound", [["purr", "Purr"], ["chimes", "Wind chimes"], ["bubbles", "Bubbles"], ["off", "Silent"]], "Petting sound"),
            h("div", { class: "row" }, volume,
              h("button", { class: "ghost", onclick: () => { const s = sound(); if (pet.sound === "purr") s._purr(0.8); else for (let i = 0; i < 5; i++) setTimeout(() => s.pet(100, 0.5), i * 170); } }, "Try petting"),
              h("button", { class: "ghost", onclick: () => sound().play("alert") }, "Hear an alert"),
              h("button", { class: "ghost", onclick: () => sound().play("ok") }, "Hear a success")))))));
}

// ====================================================================== router
const ALIASES = { overview: "home", rules: "sensitive", connectors: "claude" };
let firstRoute = true;
async function route() {
  let name = (location.hash.replace("#", "").split("&")[0]) || "home";
  name = ALIASES[name] || name;
  const view = views[name] ? name : "home";
  document.querySelectorAll("#nav a[data-view]").forEach((a) => a.classList.toggle("on", a.dataset.view === view));
  document.querySelectorAll(".dock a[data-view]").forEach((a) => a.classList.toggle("on", a.dataset.view === view || (a.dataset.view === "more" && MORE.includes(view))));
  document.body.classList.toggle("is-home", view === "home");
  closeMore();
  try {
    if (firstRoute) {
      firstRoute = false;
      const s = await refreshState();
      if (!s.onboarded) return onboarding(0);
    }
    await views[view]();
  } catch (e) {
    page("Can't reach Walled", { icon: "lock", text: "" }, h("div", { class: "notice" }, e.message === "unauthorised" ? "This window's session expired. Open Walled again from the app, the side panel, or run “walled app”." : e.message));
  }
}

// ------------------------------------------------------------------ dock + "more" sheet
const MORE = ["claude", "tests", "activity", "settings"];
function closeMore() { const m = document.querySelector(".more-sheet"); if (m) m.remove(); }
document.querySelectorAll(".dock a").forEach((a) => a.prepend(a.dataset.view === "home" ? pipFace() : icon(a.dataset.icon, 20)));
document.querySelector(".dock a[data-view='more']").addEventListener("click", (e) => {
  e.preventDefault();
  if (document.querySelector(".more-sheet")) return closeMore();
  document.body.append(h("div", { class: "more-sheet", role: "menu" },
    ...[["claude", "spark", "Claude tools"], ["tests", "flask", "Verification"], ["activity", "pulse", "Audit log"], ["settings", "gear", "Settings"]]
      .map(([v, ic, label]) => h("a", { href: `#${v}`, role: "menuitem", onclick: closeMore }, icon(ic, 18), label))));
});
document.addEventListener("pointerdown", (e) => { if (!e.target.closest(".more-sheet, .dock")) closeMore(); });
document.querySelectorAll("#nav a").forEach((a) => a.prepend(icon(a.dataset.icon, 17)));
document.getElementById("side-pip").append(pipFace(30));
topPip = new Pip({ size: 34, mini: true, interactive: false, keep: true });
document.getElementById("top-pip").append(topPip.wrap);
window.addEventListener("hashchange", route);
redeemTicket().finally(() => {
  if (!TOKEN) page("Open Walled from the app", { icon: "lock", text: "This window needs a fresh launch link. Open Walled from Launchpad / Start, the Chrome side panel, or run “walled app”." });
  else { route(); pollEvents(); }
});
