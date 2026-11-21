/* Pip inside the Walled app: the same character and expressions as the corner pet
 * (pip-art.js), used small: the home greeting, the top bar, the dock and the setup quest. */
"use strict";

const MOOD_EXPR = { idle: "neutral", celebrate: "cheer" };     // older mood names → expressions

class Pip {
  constructor({ size = 64, interactive = true, mini = true, keep = false } = {}) {
    PipArt.adopt(document);
    this.el = PipArt.figure("neutral", size, true);
    this.wrap = h("div", { class: "pip-wrap" }, this.el);
    this.mood = "neutral";
    this.keep = keep;
    this.timers = {};
    this.handlers = [];
    this.mini = mini;
    this._blinkLoop();
    if (interactive) {
      const follow = (e) => this._follow(e);
      this.handlers = [["pointermove", follow]];
      window.addEventListener("pointermove", follow, { passive: true });
      this.el.addEventListener("click", () => this.react("happy", 1600));
      this.el.addEventListener("pointerenter", () => { if (this.mood === "neutral") this.react("shy", 1400); });
    }
  }

  _alive() {
    if (this.dead) return false;
    if (this.wrap.isConnected) { this.seen = true; return true; }
    if (this.keep || !this.seen) return true;
    this.destroy();
    return false;
  }

  destroy() {
    this.dead = true;
    Object.values(this.timers).forEach((t) => clearTimeout(t));
    this.handlers.forEach(([t, fn]) => window.removeEventListener(t, fn));
  }

  /** Any expression from PipArt (or an older mood name). */
  set(mood) {
    const expr = MOOD_EXPR[mood] || (PipArt.EXPR[mood] ? mood : "neutral");
    if (expr !== this.mood) this.el.set(expr);
    this.mood = expr;
  }

  react(mood, ms = 2600) {
    this.set(mood);
    const expr = this.mood;
    clearTimeout(this.timers.react);
    this.timers.react = setTimeout(() => { if (this.mood === expr) this.set("neutral"); }, ms);
  }

  _blinkLoop() {
    const blink = () => {
      if (!this._alive()) return;
      if (["neutral", "alert", "protect", "surprised", "busy"].includes(this.mood)) {
        this.el.classList.add("blink");
        setTimeout(() => this.el.classList.remove("blink"), 130);
      }
      this.timers.blink = setTimeout(blink, 2400 + Math.random() * 4200);
    };
    this.timers.blink = setTimeout(blink, 1200);
  }

  _follow(e) {
    if (REDUCED) return;
    const r = this.el.getBoundingClientRect();
    if (!r.width) return;
    const dx = (e.clientX - (r.left + r.width / 2)) / window.innerWidth;
    const dy = (e.clientY - (r.top + r.height / 2)) / window.innerHeight;
    this.el.style.setProperty("--lx", `${Math.max(-1, Math.min(1, dx * 2)) * 4}px`);
    this.el.style.setProperty("--ly", `${Math.max(-1, Math.min(1, dy * 2)) * 2.5}px`);
  }
}

/** Pip's speech bubble with a gentle typing effect. */
function speech() {
  const el = h("div", { class: "pip-say", role: "status", "aria-live": "polite" });
  let token = 0;
  el.say = async (text) => {
    const mine = ++token;
    el.classList.remove("pop"); void el.offsetWidth; el.classList.add("pop");
    if (REDUCED) { el.textContent = text; return; }
    el.textContent = "";
    for (let i = 1; i <= text.length; i++) {
      if (mine !== token) return;
      el.textContent = text.slice(0, i);
      await sleep(text.length > 90 ? 8 : 16);
    }
  };
  return el;
}

/** Map a service event to Pip's expression and words in the app. */
function pipReaction(e) {
  const d = e.data || {};
  if (e.type === "guard.decision") {
    if (d.decision === "cancel") return ["protect", `I held back your ${d.kind || "message"} on ${d.site}. ${e.text ? `(${e.text})` : ""}`.trim()];
    if (d.decision === "block") return ["warn", `Stopped by your rule on ${d.site}. ${e.text ? `(${e.text})` : ""}`.trim()];
    return d.decision === "warn" ? ["surprised", `Let that through with a note on ${d.site}.`] : ["thumbs", `You chose Continue — sent on ${d.site}.`];
  }
  if (e.type === "claude.waiting") return ["alert", "Claude is waiting for your Allow or Block on Telegram."];
  if (e.type === "telegram.paired") return ["cheer", "We're connected on Telegram! I'll ask you there when I need you."];
  if (e.type === "test.passed") return ["thumbs", `${e.title.replace("Test passed: ", "")} — works! ✓`];
  if (e.type === "extension.connected") return ["happy", "I can see your browser now. ChatGPT, Perplexity and Claude are under my watch."];
  if (e.type === "vault.created") return ["cheer", "Your password vault is ready."];
  if (e.type === "vault.locked") return ["sleep", "Vault locked. Nobody gets a password now."];
  if (e.tone === "bad") return ["sad", e.title];
  if (e.tone === "ok") return ["happy", e.title];
  return null;
}

/** A small, still Pip for the dock and icons. */
function pipFace(size = 24) {
  PipArt.adopt(document);
  const f = PipArt.figure("neutral", size, false);
  f.classList.add("pip-face");
  return f;
}
