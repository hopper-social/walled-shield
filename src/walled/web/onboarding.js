/* Guided setup: short steps, each with real verification you can run.
 *
 * XP comes from the server's checklist (agent registered, extension seen, first catch,
 * Telegram paired, connectors guarded, vault created…), so clicking Next earns nothing.
 * You can go Back, jump to any step from the rail, skip anything, and replay it later.
 */
"use strict";

const QUESTS = [
  { id: "welcome", title: "Welcome", xp: 0 },
  { id: "browser", title: "Connect your browser", xp: 30, goal: "browser" },
  { id: "catch", title: "Verify detection", xp: 10, goal: "browser" },
  { id: "telegram", title: "Pair Telegram", xp: 20 },
  { id: "claude", title: "Guard Claude tools", xp: 15, goal: "claude" },
  { id: "vault", title: "Create your vault", xp: 10, goal: "passwords" },
  { id: "finish", title: "Review", xp: 0 },
];

async function onboarding(startAt = 0) {
  document.querySelector(".quest")?.remove();
  document.body.classList.add("onboarding");
  const shell = h("div", { class: "quest", role: "dialog", "aria-label": "Set up Walled" });
  const rail = h("ol", { class: "quest-rail" });
  const bar = h("div", { class: "xpbar" }, h("i"));
  const xpText = h("span", { class: "xp-text" });
  const levelChip = h("span", { class: "chip acc level-chip" });
  const stage = h("section", { class: "quest-stage" });
  const guide = new Pip({ size: 40, mini: true, interactive: false });
  shell.append(h("header", { class: "quest-head" },
    h("div", { class: "brand-mini" }, guide.wrap, h("b", {}, "Walled")),
    h("div", { class: "xp" }, levelChip, bar, xpText),
    h("button", { class: "ghost", onclick: () => finish() }, "Skip setup")), h("div", { class: "quest-body" }, rail, stage));
  document.body.append(shell);

  let goals = (state && state.goals) || ["browser", "claude", "passwords"];
  let lastXp = null;
  let index = startAt;
  const done = new Set();
  const quests = () => QUESTS.filter((q) => !q.goal || goals.includes(q.goal));

  async function refreshXp() {
    const ins = await api("/api/insights");
    const s = ins.score;
    bar.firstChild.style.width = `${s.percent}%`;
    xpText.textContent = `Setup ${s.percent}%`;
    levelChip.textContent = s.level;
    lastXp = s.xp;
    return ins;
  }

  function paintRail() {
    rail.replaceChildren(...quests().map((q, i) => h("li", { class: i === index ? "on" : done.has(q.id) || i < index ? "done" : "",
      tabindex: "0", role: "button", onclick: () => go(i), onkeydown: (e) => { if (e.key === "Enter") go(i); } },
      h("span", {}, (done.has(q.id) || i < index) && i !== index ? "✓" : i + 1), q.title)));
  }

  let started = false;
  async function go(i) {
    if (started && index < quests().length) done.add(quests()[index].id);
    started = true;
    index = Math.max(0, Math.min(i, quests().length - 1));
    paintRail();
    stage.classList.remove("enter"); void stage.offsetWidth; stage.classList.add("enter");
    refreshXp().catch(() => {});
    const q = quests()[index];
    guide.react(q.id === "finish" ? "celebrate" : "think", q.id === "finish" ? 3000 : 900);
    await (STEPS[q.id] || STEPS.finish)();
  }
  const next = () => go(index + 1);
  const back = () => go(index - 1);
  const nav = (label = "Continue") => h("div", { class: "quest-nav" },
    index > 0 ? h("button", { class: "ghost back", onclick: back }, "← Back") : null,
    h("button", { class: "ghost", onclick: next }, "Skip for now"), h("button", { class: "primary", onclick: next }, `${label} →`));

  function liveCheck(text, test, onOk, okText) {
    const row = h("div", { class: "live" }, h("i", { class: "dot-live" }), h("span", {}, text));
    (async () => {
      let first = true;
      while (shell.isConnected && (first || stage.contains(row))) {
        first = false;
        if (await test().catch(() => false)) {
          row.replaceChildren(checkMark(24), h("span", {}, okText || "Done"));
          row.classList.add("ok");
          guide.react("celebrate", 2400);
          refreshXp().catch(() => {});
          if (onOk) onOk();
          return;
        }
        await sleep(1500);
      }
    })();
    return row;
  }

  /** A small "try it" row: title, explanation, button that runs a real test and shows the result. */
  function tryRow(title, text, label, runTest) {
    const out = h("span", { class: "muted small" });
    return h("div", { class: "try" }, h("div", { class: "grow" }, h("b", {}, title), h("span", { class: "muted small" }, text), h("div", {}, out)),
      h("button", { onclick: async (e) => {
        e.currentTarget.disabled = true;
        try { out.textContent = await runTest(); } catch (err) { out.textContent = err.message; }
        e.currentTarget.disabled = false;
        refreshXp().catch(() => {});
      } }, label));
  }

  const STEPS = {
    async welcome() {
      const choice = (id, ic, title, text) => {
        const on = goals.includes(id);
        const b = h("button", { class: `goal ${on ? "on" : ""}`, "aria-pressed": String(on), onclick: () => {
          goals = goals.includes(id) ? goals.filter((g) => g !== id) : [...goals, id];
          b.classList.toggle("on"); b.setAttribute("aria-pressed", String(goals.includes(id))); paintRail();
        } }, h("span", { class: "goal-icon" }, icon(ic, 20)), h("b", {}, title), h("small", {}, text));
        return b;
      };
      const hello = new Pip({ size: 72, interactive: true, mini: true });
      const bubble = speech();
      setTimeout(() => { hello.react("happy", 1800); bubble.say("Hi, I'm Pip! I'll live quietly in a corner of your screen and tap your shoulder before anything sensitive leaves."); }, 250);
      stage.replaceChildren(h("div", { class: "pip-hello" }, hello.wrap, bubble), h("h1", {}, "Let's shield what you share with AI"),
        h("p", { class: "lede" }, "About 5 minutes. Each step includes a verification you can run, so you know every control works. Choose what to protect:"),
        h("div", { class: "goals" },
          choice("browser", "globe", "ChatGPT, Perplexity & Claude", "Check prompts and uploads before they leave your browser"),
          choice("claude", "spark", "Claude tools (MCP)", "Approve Claude Desktop tool calls from your phone"),
          choice("passwords", "key", "Passwords", "Strong passwords; fills you approve")),
        h("div", { class: "explain", style: "margin-top:14px" }, icon("bolt", 16), h("span", {}, "Tip: after the browser step, open Walled in the Chrome side panel (Walled icon → “Open Walled in the side panel”) so this guide stays next to your tabs.")),
        h("div", { class: "quest-nav" }, h("button", { class: "primary big", onclick: async () => {
          if (!goals.length) return toast("Pick at least one", true);
          await api("/api/onboarding", { method: "PUT", body: { goals } });
          next();
        } }, "Start →")));
    },

    async browser() {
      let info = await api("/api/browser");
      stage.replaceChildren(h("h1", {}, "Connect your browser"),
        h("p", { class: "lede" }, "The extension watches the chats; the desktop agent reads files and shows the dialog. Walled already set up the agent for you."),
        h("section", { class: "card stack" },
          h("div", { class: `live ${info.browsers.length ? "ok" : ""}` }, info.browsers.length ? checkMark(24) : h("i", { class: "dot-live" }),
            h("span", {}, info.browsers.length ? `Desktop agent ready in ${info.browsers.join(", ")}` : "Open Chrome once, then come back — Walled connects to it automatically.")),
          extensionSteps(info.extension_path),
          liveCheck("Waiting for the extension to say hi…", async () => (await api("/api/browser")).extension_seen, null,
            "Extension connected — ChatGPT, Perplexity and Claude are protected"),
          tryRow("Test the desktop dialog", "See exactly what appears when something sensitive is caught.", "Show test dialog",
            async () => { const r = await api("/api/tests/dialog", { body: {} }); return `✓ The dialog works (you chose ${r.choice}).`; })),
        nav());
    },

    async catch() {
      const since = Date.now() / 1000 - 5;
      stage.replaceChildren(h("h1", {}, "Verify detection"),
        h("p", { class: "lede" }, "Now try it for real. Each test below passes on its own as soon as it happens in your browser."),
        h("section", { class: "card stack" },
          h("div", { class: "try" }, h("div", { class: "grow" }, h("b", {}, "1 · A prompt"), h("span", { class: "muted small" }, "Paste this into ChatGPT, Perplexity or Claude and press Enter. Choose Cancel — your text stays in the box.")),
            h("div", { class: "code-big", style: "font-size:13px" }, "My notes WALLED_SENSITIVE", copyBtn("My notes WALLED_SENSITIVE"))),
          h("div", { class: "row" }, h("a", { class: "btn", href: "https://chatgpt.com", target: "_blank", rel: "noopener" }, "Open ChatGPT"),
            h("a", { class: "btn", href: "https://www.perplexity.ai", target: "_blank", rel: "noopener" }, "Perplexity"),
            h("a", { class: "btn", href: "https://claude.ai", target: "_blank", rel: "noopener" }, "Claude")),
          liveCheck("Waiting for your first catch…", async () => {
            const r = await api("/api/activity");
            return r.events.some((e) => e.source === "browser" && new Date(fmtIso(e.time)) / 1000 >= since);
          }, null, "Verified: the prompt was intercepted before it was sent"),
          h("div", { class: "try" }, h("div", { class: "grow" }, h("b", {}, "2 · A file (optional)"), h("span", { class: "muted small" }, "Download a sample and upload it to a chat. Walled reads PDFs page by page and text inside screenshots.")),
            h("a", { class: "btn", href: "/samples/secret_report.pdf", download: "secret_report.pdf" }, "Sample PDF"),
            h("a", { class: "btn", href: "/samples/secret_screenshot.png", download: "secret_screenshot.png" }, "Sample screenshot"))),
        nav());
    },

    async telegram() {
      const box = h("section", { class: "card" });
      const tests = h("div", { class: "stack", style: "margin-top:12px" });
      const t = (await refreshState()).telegram;
      const showTests = () => tests.replaceChildren(
        tryRow("Test Allow / Block", "A test card arrives in your bot. Tap either button.", "Send test card",
          async () => { const r = await api("/api/tests/telegram", { body: {} }); return r.ok ? `✓ Works — you tapped ${r.choice === "allow" ? "Allow" : "Block"}.` : `No answer (${r.reason}).`; }),
        tryRow("Test the race", "The same question on this computer and your phone. Answer on either — the other closes by itself.", "Start race",
          async () => { const r = await api("/api/tests/race", { body: {} }); return r.ok ? `✓ First answer came from the ${r.via === "phone" ? "phone" : "computer"}; the other was withdrawn.` : `No answer (${r.reason}).`; }));
      stage.replaceChildren(h("h1", {}, "Pair Telegram 📱"),
        h("p", { class: "lede" }, "One private bot for Claude approvals, sensitive-data alerts and password fills. Telegram only ever sees masked text."), box, tests, nav());
      if (t.running) { box.replaceChildren(h("div", { class: "done-stage" }, checkMark(56), h("b", {}, `Paired with @${t.bot || "your bot"}`))); showTests(); }
      else pairingFlow(box, { compact: true, onDone: () => { refreshXp(); showTests(); } });
    },

    async claude() {
      const c = await api("/api/connectors");
      const entries = Object.entries(c.servers || {});
      const list = h("div");
      const paint = (servers) => list.replaceChildren(...Object.entries(servers).map(([n, s]) =>
        h("div", { class: "list-item" }, h("span", { class: `dot ${s === "guarded" ? "ok" : s === "UNGUARDED" ? "bad" : ""}` }), h("div", { class: "grow" }, h("b", {}, n)),
          h("span", { class: `chip ${s === "guarded" ? "ok" : s === "UNGUARDED" ? "bad" : ""}` }, s === "UNGUARDED" ? "not guarded" : s))));
      paint(c.servers || {});
      stage.replaceChildren(h("h1", {}, "Guard Claude tools 🛠"),
        h("p", { class: "lede" }, "Every guarded Claude Desktop tool call waits for Allow on your phone."),
        h("section", { class: "card stack" }, entries.length ? list : h("div", { class: "empty" }, "No Claude Desktop MCP servers found. You can do this later from Claude tools."),
          entries.length ? h("button", { class: "primary", onclick: async () => {
            const r = await run(() => api("/api/connectors/wrap", { body: {} }));
            paint(r.servers); refreshXp(); if (r.changed) toast("Quit and reopen Claude Desktop to apply.");
          } }, "Guard all servers") : null,
          entries.length ? h("div", { class: "explain" }, icon("flask", 16), h("span", {}, "Test: reopen Claude Desktop, ask it to use one of these tools, and tap Allow on Telegram. The Test center marks it passed.")) : null),
        nav());
    },

    async vault() {
      const v = await api("/api/vault");
      const body = h("section", { class: "card" });
      stage.replaceChildren(h("h1", {}, "Create your vault"),
        h("p", { class: "lede" }, "Strong, unique passwords on sign-ups and fills you approve. Encrypted on this computer only."), body, nav());
      const after = () => body.replaceChildren(h("div", { class: "done-stage" }, checkMark(56), h("b", {}, "Vault ready")),
        h("div", { class: "explain" }, icon("flask", 16), h("span", {}, "Test: in the Walled extension popup turn on “Suggest & fill passwords”, then open the ",
          h("a", { href: "/practice/signup", target: "_blank" }, "practice sign-up page"), ".")));
      if (v.exists) return after();
      body.replaceChildren(vaultChoices(v, () => { refreshXp(); after(); }));
    },

    async finish() {
      const ins = await refreshXp();
      const { tests } = await api("/api/tests");
      const passed = Object.values(tests).filter((t) => t.ok).length;
      stage.replaceChildren(h("div", { class: "finale" }, scoreRing(ins.score.percent, 150),
        h("h1", {}, "Setup complete"),
        h("p", { class: "lede" }, `Security posture: ${ins.score.level}. ${passed} of ${Object.keys(tests).length} verifications passed — run the rest any time from the Test center.`),
        h("div", { class: "quest-nav", style: "justify-content:center" }, h("button", { class: "ghost", onclick: back }, "← Back"),
          h("a", { class: "btn", href: "#tests", onclick: () => finish("#tests") }, "Open Test center"),
          h("button", { class: "primary big", onclick: () => finish() }, "Go to dashboard"))));
    },
  };

  async function finish(target = "#home") {
    await api("/api/onboarding", { method: "PUT", body: { onboarded: true, goals } }).catch(() => {});
    shell.remove();
    document.body.classList.remove("onboarding");
    location.hash = target;
    route();
  }

  go(index);
}

function fmtIso(t) { return String(t).replace(/([+-]\d\d)(\d\d)$/, "$1:$2"); }
