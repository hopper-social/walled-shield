/* Telegram pairing, renewal and the activity channel, as small animated steps.
 *
 * Two ways in:
 *   A  Guided in Telegram Web (recommended): the browser extension opens web.telegram.org and
 *      walks you through @BotFather there; it spots the token and connects it for you.
 *   B  Paste a token you already have.
 * Both end with a one-tap "Open in Telegram → START" deep link, a 2-minute ring and a ✓.
 */
"use strict";

const TOKEN_RE = /^\d{5,12}:[A-Za-z0-9_-]{30,64}$/;

function stepper(labels, active) {
  return h("ol", { class: "stepper" }, labels.map((l, i) =>
    h("li", { class: i < active ? "done" : i === active ? "on" : "" }, h("span", {}, i < active ? "✓" : i + 1), l)));
}

function pairingFlow(container, { onDone, compact } = {}) {
  const labels = ["Create bot", "Connect token", "Press Start", "Paired"];
  const render = (active, body) => container.replaceChildren(stepper(labels, active), h("div", { class: "pair-stage" }, body));
  let stopped = false;

  // Live: the extension may connect a token while this screen is open.
  onEvent((e) => {
    if (stopped || !container.isConnected) return;
    if (e.type === "telegram.pairing") api("/api/telegram/pairing").then(stageWaiting).catch(() => {});
    if (e.type === "telegram.paired") { stopped = true; stageDone(e.data.bot); }
  });

  const stageChoose = () => {
    render(0, h("div", { class: "stack" },
      h("div", { class: "grid g2" },
        h("button", { class: "method", onclick: async () => {
          const r = await run(() => api("/api/telegram/web-setup", { body: {} }));
          if (!r.extension) { window.open("https://web.telegram.org/k/", "_blank", "noopener"); toast("Load the Walled extension for the guided steps — or paste the token below."); }
          stageWeb(!!r.extension);
        } }, h("span", { class: "chip acc rec" }, "Recommended"), h("b", {}, "Guided in Telegram Web"),
          h("small", {}, "Walled opens web.telegram.org in your browser, checks you're logged in, and walks you through @BotFather with small cards. It picks up the token by itself.")),
        h("button", { class: "method", onclick: () => stageToken() }, h("span", { class: "chip rec" }, "I have a token"), h("b", {}, "Paste a bot token"),
          h("small", {}, "Already made a bot with @BotFather? Paste its token (123456:AA…). Walled keeps it in a private file on this computer."))),
      compact ? null : h("div", { class: "explain" }, icon("shield", 16),
        h("span", {}, "Your bot is yours alone. Walled sends it only masked alerts and Allow/Block questions — never prompts, files or passwords."))));
  };

  const stageWeb = (hasExtension) => {
    render(0, h("div", { class: "waiting" },
      h("div", { class: "bot-row" }, h("div", { class: "avatar pulse" }, "🧭"),
        h("div", {}, h("b", {}, "Continue in Telegram Web"),
          h("div", { class: "muted small" }, hasExtension ? "Follow the Walled cards in the Telegram tab. This screen updates by itself." : "Create a bot with @BotFather, then copy the token — Walled offers to connect it."))),
      h("ol", { class: "steps-mini muted small" },
        h("li", {}, "Log in to Telegram Web (QR code from your phone)."),
        h("li", {}, "Walled asks permission, then opens @BotFather and fills /newbot, a name and a username."),
        h("li", {}, "BotFather replies with a token → Walled connects it → you press START.")),
      h("div", { class: "row" }, h("button", { class: "ghost", onclick: stageChoose }, "← Back"), h("button", { class: "ghost", onclick: () => stageToken() }, "Paste a token instead"))));
  };

  const stageToken = (error) => {
    const field = secretInput({ placeholder: "123456789:AAH…", "aria-label": "Bot token" });
    const input = field.input;
    const hint = h("span", { class: "muted small" }, "Paste the token @BotFather sent you.");
    const go = h("button", { class: "primary", disabled: true }, "Connect");
    input.addEventListener("input", () => {
      const ok = TOKEN_RE.test(input.value.trim());
      go.disabled = !ok;
      input.classList.toggle("token-ok", ok);
      hint.textContent = !input.value ? "Paste the token @BotFather sent you." : ok ? "Looks right ✓" : "A token looks like 123456789:AAH… (digits, a colon, then letters).";
    });
    input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !go.disabled) go.click(); });
    go.addEventListener("click", async () => {
      go.disabled = true; go.textContent = "Connecting…";
      try {
        const snap = await api("/api/telegram/setup", { body: { token: input.value.trim() } });
        input.value = "";
        stageWaiting(snap);
      } catch (e) { stageToken(e.message); }
    });
    render(1, h("div", { class: "stack" + (error ? " shake" : "") },
      h("div", { class: "row" }, field, go), hint,
      error ? h("div", { class: "notice" }, error) : null,
      h("div", { class: "row" }, h("button", { class: "ghost", onclick: stageChoose }, "← Back"),
        h("a", { class: "btn ghost", href: "https://web.telegram.org/k/#@BotFather", target: "_blank", rel: "noopener" }, "Open @BotFather"))));
    setTimeout(() => input.focus(), 50);
  };

  let waitingLoop = 0;
  const stageWaiting = async (snap) => {
    const loop = ++waitingLoop;
    const ring = countdownRing(120);
    const codeBox = h("div", { class: "code-big" }, h("span", { class: "code" }, "…"), copyBtn(() => snap.code));
    const open = h("a", { class: "btn primary big", target: "_blank", rel: "noopener" }, "Open in Telegram");
    render(2, h("div", { class: "waiting" },
      h("div", { class: "bot-row" }, h("div", { class: "avatar pulse" }, "🤖"), h("div", {}, h("b", { class: "bot-name" }, "@…"),
        h("div", { class: "muted small" }, h("i", { class: "dot-live" }), "Listening for START…")), ring),
      open, h("div", { class: "muted small" }, "Tap START in the chat that opens. Or send this message to the bot:"), codeBox,
      h("button", { class: "ghost small", onclick: () => { waitingLoop++; stageToken(); } }, "Use a different token")));
    for (;;) {
      if (loop !== waitingLoop || stopped) return;
      if (snap.state === "done") { stopped = true; return stageDone(snap.bot); }
      if (snap.state === "error") return stageToken(snap.error);
      container.querySelector(".bot-name").textContent = snap.bot ? `@${snap.bot}` : "your bot";
      codeBox.querySelector(".code").textContent = snap.code || "…";
      if (snap.link) open.href = snap.link; else open.removeAttribute("href");
      ring.set(snap.seconds_left ?? 120);
      await sleep(1000);
      if (!container.isConnected) return;
      snap = await api("/api/telegram/pairing").catch(() => snap);
    }
  };

  const stageDone = (bot) => {
    render(4, h("div", { class: "done-stage" }, checkMark(76), h("h2", {}, `Paired with @${bot || "your bot"}`),
      h("p", { class: "muted" }, "Approvals are on for 24 hours. A welcome message is waiting in Telegram.")));
    confetti();
    if (onDone) setTimeout(onDone, 1500);
  };

  api("/api/telegram/pairing").then((snap) => {
    if (snap.state === "waiting" && snap.code) stageWaiting(snap); else stageChoose();
  }).catch(() => stageChoose());
}

function renewFlow(container, { onDone } = {}) {
  const ring = countdownRing(120);
  const codeBox = h("div", { class: "code-big" }, h("span", { class: "code" }, "…"));
  const open = h("a", { class: "btn primary", target: "_blank", rel: "noopener" }, "Open in Telegram");
  container.replaceChildren(h("div", { class: "waiting" },
    h("div", { class: "bot-row" }, h("div", { class: "avatar pulse" }, "🔓"),
      h("div", {}, h("b", {}, "Turn approvals on"), h("div", { class: "muted small" }, h("i", { class: "dot-live" }), "Waiting for START…")), ring),
    open, h("div", { class: "muted small" }, "or send:"), codeBox));
  (async () => {
    let st = await run(() => api("/api/telegram/renew", { body: {} }));
    let code = st.code;
    for (;;) {
      if (st.state === "done") {
        container.replaceChildren(h("div", { class: "done-stage" }, checkMark(56), h("b", {}, "Approvals on for 24 hours")));
        if (onDone) setTimeout(onDone, 1200);
        return;
      }
      if (st.state === "expired" || (!st.code && st.state !== "waiting")) {
        container.replaceChildren(h("div", { class: "notice" }, "The code expired. ", h("button", { onclick: () => renewFlow(container, { onDone }) }, "Try again")));
        return;
      }
      code = st.code || code;
      codeBox.querySelector(".code").textContent = code;
      if (!codeBox.querySelector("button")) codeBox.append(copyBtn(() => code));
      if (st.link) open.href = st.link;
      ring.set(st.seconds_left);
      await sleep(1000);
      if (!container.isConnected) return;
      st = await api("/api/telegram/renew").catch(() => st);
    }
  })();
}

/** Pair a separate group/channel for the activity feed (startgroup deep link or /alertpair CODE). */
function activityFlow(container, { onDone } = {}) {
  const ring = countdownRing(120);
  const codeBox = h("div", { class: "code-big" }, h("span", { class: "code" }, "…"));
  const open = h("a", { class: "btn primary", target: "_blank", rel: "noopener" }, "Add bot to a group");
  container.replaceChildren(stepper(["Pick a group", "Add the bot", "Connected"], 1), h("div", { class: "waiting" },
    h("div", { class: "bot-row" }, h("div", { class: "avatar pulse" }, "📒"),
      h("div", {}, h("b", {}, "Choose (or create) a group for logs"), h("div", { class: "muted small" }, h("i", { class: "dot-live" }), "Waiting for the group…")), ring),
    open, h("div", { class: "muted small" }, "Telegram asks which group to add the bot to. For a channel: add the bot as admin and post:"), codeBox));
  (async () => {
    let st = await run(() => api("/api/telegram/activity", { body: {} }));
    for (;;) {
      if (st.state === "done") {
        container.replaceChildren(stepper(["Pick a group", "Add the bot", "Connected"], 3),
          h("div", { class: "done-stage" }, checkMark(56), h("b", {}, "Activity channel connected")));
        confetti(40);
        if (onDone) setTimeout(onDone, 1400);
        return;
      }
      if (st.state === "error") { container.replaceChildren(h("div", { class: "notice" }, `Didn't finish: ${st.error}`)); return; }
      codeBox.querySelector(".code").textContent = st.code || "…";
      if (!codeBox.querySelector("button") && st.code) codeBox.append(copyBtn(() => st.code));
      if (st.link) open.href = st.link;
      ring.set(st.seconds_left ?? 120);
      await sleep(1000);
      if (!container.isConnected) return;
      st = await api("/api/telegram/activity").catch(() => st);
    }
  })();
}
