/* Pip, Walled's little guardian: a pocket-sized pet that lives in a corner.
 *
 * One runtime for two homes: a transparent always-on-top window on the desktop (buddy.html) and
 * the corner of ChatGPT / Perplexity / Claude (buddy-browser.js, inside a shadow root).
 * His look and 16 expressions come from pip-art.js (load it first).
 *
 *  Mostly unnoticed : stands guard, blinks, glances, paces, plays, naps, sometimes vanishes.
 *  Stroke him       : shy → love, with the petting sound you chose (purr, chimes, bubbles).
 *  Click him        : one small panel (status, sound, mood, open Walled, nap, hide).
 *  When it matters  : he hops up with a clear note that shows how he feels, plus a soft sound.
 *
 * All text goes in with textContent; SVG comes from fixed templates.
 */
(() => {
  "use strict";
  if (globalThis.WalledBuddy) return;
  const Art = globalThis.PipArt;

  const REDUCED = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (list) => list[Math.floor(Math.random() * list.length)];
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  // ------------------------------------------------------------------------------- sound
  /** Soft, synthesized sounds (no audio files). Everything passes a gentle low-pass. */
  class Sound {
    constructor() { this.ctx = null; this.theme = "purr"; this.volume = 0.45; this.mood = "calm"; this.played = []; this.purrLevel = 0; this.buffers = {}; }

    /**
     * Use recorded sounds where they exist. `base` is a folder URL holding index.json (a list of
     * file names such as "alert.mp3") and the files. Anything missing keeps its synthesized version.
     * Names: alert knock ok info hello boop giggle love grumble beg purr(loop) chime bubble.
     */
    async useFiles(base) {
      if (!base) return;
      try {
        const res = await fetch(`${base}/index.json`, { cache: "no-store" });
        if (!res.ok) return;
        const files = await res.json();
        this._files = Object.fromEntries((Array.isArray(files) ? files : []).filter((f) => /^[a-z]+\.(mp3|ogg|wav|m4a)$/.test(f)).map((f) => [f.split(".")[0], `${base}/${f}`]));
      } catch { /* no recorded sounds: synthesized ones are used */ }
    }

    async _buffer(kind) {
      if (!this._files || !this._files[kind]) return null;
      if (this.buffers[kind] !== undefined) return this.buffers[kind];
      this.buffers[kind] = null;
      try {
        const data = await (await fetch(this._files[kind])).arrayBuffer();
        this.buffers[kind] = await this._ctx().decodeAudioData(data);
      } catch { this.buffers[kind] = null; }
      return this.buffers[kind];
    }

    /** Play a recorded file if there is one; returns true when it did. */
    _file(kind, gain = 1, rate = 1) {
      const buf = this.buffers[kind];
      if (buf === undefined && this._files && this._files[kind]) { this._buffer(kind); return false; }   // load for next time
      if (!buf) return false;
      const src = this.ctx.createBufferSource(), g = this.ctx.createGain();
      src.buffer = buf; src.playbackRate.value = rate; g.gain.value = gain;
      src.connect(g); g.connect(this.out); src.start();
      return true;
    }

    set({ sound, volume, mood } = {}) {
      if (sound) this.theme = sound;
      if (volume !== undefined) this.volume = Math.max(0, Math.min(1, volume / 100));
      if (mood) this.mood = mood;
      if (this.out) this.out.gain.value = this._gain();
    }

    _gain() { return Math.pow(this.volume, 1.5) * 0.7; }

    _ctx() {
      const C = globalThis.AudioContext || globalThis.webkitAudioContext;
      if (!C) return null;
      if (!this.ctx) {
        try { this.ctx = new C(); } catch { return null; }
        const soft = this.ctx.createBiquadFilter();
        soft.type = "lowpass"; soft.frequency.value = 4800;
        this.out = this.ctx.createGain(); this.out.gain.value = this._gain();
        this.out.connect(soft); soft.connect(this.ctx.destination);
      }
      if (this.ctx.state === "suspended") this.ctx.resume().catch(() => {});
      return this.ctx;
    }

    /** Wake audio on the first touch (browsers keep it asleep until a user gesture). */
    unlock() { this._ctx(); }

    _tone(f, t, dur, { type = "sine", g = 0.15, a = 0.008, to = null, dest = null } = {}) {
      const c = this.ctx, o = c.createOscillator(), v = c.createGain();
      o.type = type;
      o.frequency.setValueAtTime(f, t);
      if (to) o.frequency.exponentialRampToValueAtTime(to, t + Math.min(dur * 0.6, 0.18));
      v.gain.setValueAtTime(0.0001, t);
      v.gain.exponentialRampToValueAtTime(g, t + a);
      v.gain.exponentialRampToValueAtTime(0.0001, t + dur);
      o.connect(v); v.connect(dest || this.out);
      o.start(t); o.stop(t + dur + 0.05);
    }

    /** A little mallet: a sine plus a quiet bright partial that dies fast. */
    _bell(f, t, dur, g) { this._tone(f, t, dur, { g }); this._tone(f * 2.76, t, dur * 0.3, { g: g * 0.15 }); }

    play(kind) {
      this.played.push(kind);
      if (this.played.length > 60) this.played.shift();
      if (this.volume <= 0) return;
      const c = this._ctx();
      if (!c) return;
      const t = c.currentTime + 0.02;
      const hush = this.mood === "focus" ? 0.6 : 1;
      if (this._file(kind, hush)) return;
      switch (kind) {
        case "alert": {        // warm two-note "hey", once more softly: noticeable, not alarming
          const warm = c.createBiquadFilter(); warm.type = "lowpass"; warm.frequency.value = 1500; warm.connect(this.out);
          [[0, 1], [1.05, 0.45]].forEach(([d, k]) => {
            this._tone(440, t + d, 0.55, { type: "triangle", g: 0.26 * k * hush, a: 0.02, dest: warm });
            this._tone(659.25, t + d + 0.17, 0.75, { type: "triangle", g: 0.24 * k * hush, a: 0.02, dest: warm });
          });
          break;
        }
        case "knock": [0, 0.16].forEach((d) => this._tone(523.25, t + d, 0.22, { type: "triangle", g: 0.16 * hush, to: 392 })); break;
        case "ok": [1046.5, 1318.5, 1568, 2093].forEach((f, i) => this._bell(f, t + i * 0.075, 0.55, 0.11 * hush)); break;
        case "info": this._bell(1318.5, t, 0.5, 0.11 * hush); this._bell(1975.5, t + 0.12, 0.6, 0.09 * hush); break;
        case "hello": this._tone(784, t, 0.2, { g: 0.09 * hush, to: 1175 }); this._bell(1568, t + 0.17, 0.45, 0.06 * hush); break;
        case "boop": this._tone(620, t, 0.15, { g: 0.16, to: 990 }); break;
        case "giggle": [0, 0.09, 0.18].forEach((d, i) => this._tone(880 + i * 110, t + d, 0.1, { g: 0.1, to: 1200 + i * 120 })); break;
        case "love": [1568, 1975.5, 2349.3, 3136].forEach((f, i) => this._bell(f, t + i * 0.09, 0.8, 0.055)); break;
        case "yawn": this._tone(392, t, 0.7, { g: 0.05 * hush, to: 247 }); break;
        case "grumble": [0, 0.16].forEach((d, i) => this._tone(196 - i * 30, t + d, 0.26, { type: "triangle", g: 0.12, to: 150 - i * 20 })); break;
        case "beg": this._tone(660, t, 0.16, { g: 0.08, to: 880 }); this._tone(880, t + 0.2, 0.22, { g: 0.07, to: 1175 }); break;
        default: break;
      }
    }

    /** Petting: called as the pointer strokes him. `px` is how far it moved, `speed` px/ms. */
    pet(px, speed) {
      if (this.theme === "off" || this.volume <= 0) return;
      const c = this._ctx();
      if (!c) return;
      if (this.theme === "purr") return this._purr(Math.min(1, 0.35 + speed * 0.9));
      this._travel = (this._travel || 0) + px;
      const step = this.theme === "chimes" ? 85 : 60;
      if (this._travel < step) return;
      this._travel = 0;
      const t = c.currentTime + 0.01;
      if (this._file(this.theme === "chimes" ? "chime" : "bubble", 0.6, this.theme === "chimes" ? pick([1, 1.122, 1.26, 1.498, 1.682]) : rand(0.85, 1.25))) return;
      if (this.theme === "chimes") {             // C-major pentatonic wind chimes: always consonant
        const f = pick([1046.5, 1174.7, 1318.5, 1568, 1760, 2093, 2349.3, 2637]);
        this._bell(f, t, rand(1.2, 2.0), 0.06);
      } else {                                    // bubbles: little rising pops
        const f = rand(380, 620);
        this._tone(f, t, 0.12, { g: 0.09, to: f * rand(1.8, 2.4) });
      }
    }

    _purr(level) {
      const c = this.ctx;
      if (!this.purr && this._files && this._files.purr && this.buffers.purr === undefined) this._buffer("purr");
      if (!this.purr && this.buffers.purr) {              // recorded purr, looped
        const src = c.createBufferSource(); src.buffer = this.buffers.purr; src.loop = true;
        const g = c.createGain(); g.gain.value = 0; src.connect(g); g.connect(this.out); src.start();
        this.purr = g;
      }
      if (!this.purr) {
        const len = c.sampleRate * 2, buf = c.createBuffer(1, len, c.sampleRate), d = buf.getChannelData(0);
        let last = 0;
        for (let i = 0; i < len; i++) { last = (last + 0.02 * (Math.random() * 2 - 1)) / 1.02; d[i] = last * 3.2; }   // brown noise
        const src = c.createBufferSource(); src.buffer = buf; src.loop = true;
        const lp = c.createBiquadFilter(); lp.type = "lowpass"; lp.frequency.value = 340;
        const am = c.createGain(); am.gain.value = 0.55;
        const lfo = c.createOscillator(); lfo.frequency.value = 23;
        const depth = c.createGain(); depth.gain.value = 0.45;
        lfo.connect(depth); depth.connect(am.gain);
        const level = c.createGain(); level.gain.value = 0;
        src.connect(lp); lp.connect(am); am.connect(level); level.connect(this.out);
        src.start(); lfo.start();
        this.purr = level;
      }
      this.purr.gain.setTargetAtTime(level * 0.55, c.currentTime, 0.12);
      clearTimeout(this._purrStop);
      this._purrStop = setTimeout(() => this.stopPet(), 500);
    }

    stopPet() { if (this.purr && this.ctx) this.purr.gain.setTargetAtTime(0, this.ctx.currentTime, 0.35); }
  }

  // ------------------------------------------------------------------------------- styles
  const CSS = `
  .wb{position:absolute;inset:0;pointer-events:none;font:500 13.5px/1.45 Inter,system-ui,-apple-system,"Segoe UI",sans-serif;color:#e8ecf4;-webkit-user-select:none;user-select:none}
  .wb *{box-sizing:border-box}
  .wb-stage{position:absolute;bottom:0;width:230px;height:120px}
  .wb[data-corner="bottom-right"] .wb-stage{right:0}.wb[data-corner="bottom-left"] .wb-stage{left:0}
  .wb-pip{position:absolute;bottom:2px;width:66px;height:68px;pointer-events:auto;cursor:pointer;outline:none;transition-property:left,right;transition-timing-function:linear;--pa-size:66px}
  .wb-pip:focus-visible .pa{filter:drop-shadow(0 0 4px #2dd4bf)}
  .wb-body{width:100%;height:100%;transform-origin:50% 100%}
  .wb-pip .pa{width:100%;filter:drop-shadow(0 6px 10px rgba(5,10,20,.3))}
  .wb-pip.left .pa{transform:scaleX(-1)}
  .wb-pip .pa-eyes{transform:translate(var(--lx,0px),var(--ly,0px))}
  .wb-pip .pa.blink .pa-eyes{transform:translate(var(--lx,0px),var(--ly,0px)) scaleY(.1)}
  .wb-pip.walking .pa-fig{animation:wb-step .36s ease-in-out infinite}
  .wb-pip.hop .wb-body{animation:wb-hop .55s cubic-bezier(.3,.6,.3,1)}
  .wb-pip.boop .wb-body{animation:wb-squish .32s ease-out}
  .wb-pip.celebrate .wb-body{animation:wb-hop .55s cubic-bezier(.3,.6,.3,1) 3}
  .wb-pip.petting .wb-body{animation:wb-lean 1s ease-in-out infinite}
  .wb-pip.gone .wb-body{animation:wb-vanish .9s cubic-bezier(.6,0,.4,1) forwards}.wb-pip.gone{pointer-events:none}
  .wb-pip.arrive .wb-body{animation:wb-arrive .8s cubic-bezier(.2,1.4,.4,1) both}
  .wb-quip{position:absolute;bottom:76px;max-width:200px;pointer-events:none;background:#fff;color:#141a33;font-weight:700;font-size:13px;line-height:1.3;padding:7px 11px;border-radius:13px;box-shadow:0 10px 24px rgba(5,10,20,.35);animation:wb-pop .3s cubic-bezier(.2,1.2,.3,1) both;white-space:nowrap}
  .wb[data-corner="bottom-right"] .wb-quip{border-bottom-right-radius:4px}.wb[data-corner="bottom-left"] .wb-quip{border-bottom-left-radius:4px}
  .wb-quip.leaving{animation:wb-unpop .25s ease-in forwards}
  .wb-pip.beg .wb-body{animation:wb-beg .9s ease-in-out infinite}
  .wb-pip.stomp .wb-body{animation:wb-stomp .25s ease-in-out 4}
  .wb-pip.dance .wb-body{animation:wb-dance .5s ease-in-out 4}
  .wb-toy{position:absolute;bottom:10px;width:14px;height:14px;border-radius:50%;pointer-events:none;
          background:#f2a33a;box-shadow:0 0 10px 3px rgba(242,163,58,.45);animation:wb-toy 1.2s ease-in-out infinite}
  .wb-fx{position:absolute;inset:0;pointer-events:none;overflow:visible}
  .wb-fx i{position:absolute;font-style:normal;font-size:13px;animation:wb-float 1.3s ease-out forwards}
  .wb-fx i.heart{color:#ff6b9a}.wb-fx i.spark{color:#f2a33a;animation-name:wb-burst;animation-duration:.8s}
  /* notes and the panel */
  .wb-card{position:absolute;bottom:80px;width:310px;max-width:calc(100% - 12px);pointer-events:auto;
       background:#111a2b;border:1px solid #26354d;border-radius:20px;padding:14px 16px 14px 14px;
       box-shadow:0 24px 54px rgba(5,10,20,.5),inset 0 1px 0 rgba(255,255,255,.05);animation:wb-pop .38s cubic-bezier(.2,1.2,.3,1) both}
  .wb[data-corner="bottom-right"] .wb-card{right:8px}.wb[data-corner="bottom-left"] .wb-card{left:8px}
  .wb-card::before{content:"";position:absolute;inset:0 0 auto 0;height:3px;border-radius:20px 20px 0 0;background:var(--tone,#2dd4bf)}
  .wb-card::after{content:"";position:absolute;bottom:-7px;width:14px;height:14px;background:#111a2b;border-right:1px solid #26354d;border-bottom:1px solid #26354d;transform:rotate(45deg)}
  .wb[data-corner="bottom-right"] .wb-card::after{right:var(--tail,40px)}.wb[data-corner="bottom-left"] .wb-card::after{left:var(--tail,40px)}
  .wb-card.alert{--tone:#f2a33a}.wb-card.safe,.wb-card.ok{--tone:#2fbf86}.wb-card.info{--tone:#2dd4bf}.wb-card.bad{--tone:#ff4d5e}
  .wb-card.leaving{animation:wb-unpop .25s ease-in forwards}
  .wb-note{display:grid;grid-template-columns:56px 1fr;gap:12px;align-items:start}
  .wb-note .pa{--pa-size:56px;margin-top:2px}
  .wb-kicker{display:flex;align-items:center;gap:6px;font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:#8b97b0;font-weight:750;padding-right:18px}
  .wb-x{all:unset;position:absolute;top:10px;right:12px;cursor:pointer;color:#8b97b0;font-size:18px;line-height:1;padding:2px 4px;border-radius:6px}
  .wb-x:hover{color:#fff;background:#1f2a46}
  .wb-card h4{margin:4px 0 3px;font-size:15.5px;line-height:1.3;color:#fff;font-weight:750;letter-spacing:-.01em}
  .wb-card p{margin:0;color:#c3cbdc}
  .wb-meta{display:flex;flex-wrap:wrap;gap:5px;margin-top:9px}.wb-meta span{font-size:11.5px;font-weight:650;padding:2px 8px;border-radius:99px;background:#1f2a46;color:#d6dcea}
  .wb-meta span.hot{background:rgba(242,163,58,.16);color:#f8c56e}
  .wb-actions{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
  .wb button.b{all:unset;cursor:pointer;padding:8px 13px;border-radius:10px;font-weight:700;font-size:13px;background:#1c2a3e;color:#e6edf3}
  .wb button.b.primary{background:#0d9488;color:#fff}
  .wb button.b:hover{filter:brightness(1.12)}.wb button.b:focus-visible,.wb-chips button:focus-visible{outline:2px solid #2dd4bf;outline-offset:2px}
  .wb-panel{width:286px}
  .wb-panel .wb-hello{display:flex;gap:10px;align-items:center;padding-right:18px}.wb-panel .wb-hello .pa{--pa-size:44px}
  .wb-panel .wb-hello b{display:block;color:#fff;font-size:14.5px}.wb-panel .wb-hello small{color:#9aa6bf;font-size:12px}
  .wb-panel h5{margin:12px 0 6px;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:#8b97b0}
  .wb-chips{display:flex;flex-wrap:wrap;gap:6px}
  .wb-chips button{all:unset;cursor:pointer;padding:5px 10px;border-radius:99px;background:#1f2a46;font-weight:650;font-size:12.5px}
  .wb-chips button[aria-pressed="true"]{background:rgba(45,212,191,.16);color:#fff;box-shadow:inset 0 0 0 1px #2dd4bf}
  .wb-panel input[type=range]{width:100%;accent-color:#2dd4bf;margin:2px 0 0}
  .wb-panel .wb-actions{margin-top:14px}
  .wb-pip.spin .wb-body{animation:wb-spin .9s cubic-bezier(.4,0,.2,1)}
  .wb-pip.stretch .wb-body{animation:wb-stretch 1.3s ease-in-out}
  @keyframes wb-spin{50%{transform:translateY(-10px) rotate(180deg)}100%{transform:rotate(360deg)}}
  @keyframes wb-stretch{40%{transform:scale(.94,1.1)}70%{transform:scale(1.04,.96)}}
  @keyframes wb-beg{0%,100%{transform:translateY(0) rotate(0)}30%{transform:translateY(-8px) rotate(-6deg)}60%{transform:translateY(0) rotate(6deg)}}
  @keyframes wb-stomp{50%{transform:translateY(2px) scale(1.04,.96)}}
  @keyframes wb-dance{25%{transform:translateX(-6px) rotate(-8deg)}50%{transform:translateY(-8px)}75%{transform:translateX(6px) rotate(8deg)}}
  @keyframes wb-step{50%{transform:translateY(-2.5px)}}
  @keyframes wb-hop{40%{transform:translateY(-16px)}75%{transform:translateY(0) scale(1.08,.92)}}
  @keyframes wb-squish{30%{transform:scale(1.14,.84)}70%{transform:scale(.95,1.06)}}
  @keyframes wb-lean{0%,100%{transform:rotate(-4deg)}50%{transform:rotate(4deg)}}
  @keyframes wb-vanish{30%{transform:scale(1.08,.9)}100%{transform:scale(.05) translateY(-30px);filter:blur(6px) brightness(2);opacity:0}}
  @keyframes wb-arrive{0%{transform:scale(.05) translateY(-30px);filter:blur(6px) brightness(2);opacity:0}60%{opacity:1;filter:none}100%{transform:none}}
  @keyframes wb-toy{50%{transform:translateY(-8px) scale(.85)}}
  @keyframes wb-float{0%{opacity:0;transform:translate(0,0) scale(.6)}20%{opacity:1}100%{opacity:0;transform:translate(var(--dx,0px),-46px) scale(1.1)}}
  @keyframes wb-burst{0%{opacity:1;transform:translate(0,0) scale(.4)}100%{opacity:0;transform:translate(var(--dx),var(--dy)) scale(1)}}
  @keyframes wb-pop{from{opacity:0;transform:translateY(12px) scale(.94)}}
  @keyframes wb-unpop{to{opacity:0;transform:translateY(8px) scale(.96)}}
  @media (prefers-reduced-motion: reduce){.wb *{animation:none!important;transition:none!important}}
  `;

  // ------------------------------------------------------------------------------- stories
  const SITE_NAMES = { chatgpt: "ChatGPT", perplexity: "Perplexity", claude: "Claude" };
  /** What Pip does for an event: {expr, sound, important?, pop?} or null to stay quiet. */
  function story(e) {
    const d = e.data || {};
    const site = SITE_NAMES[d.site] || d.site || "the page";
    const rules = (d.rules || []).slice(0, 3);
    const chips = [site, ...rules.map((r) => ({ hot: r }))];
    if (e.type === "guard.decision") {
      if (d.decision === "cancel") return { expr: "protect", sound: "alert", important: true, pop: {
        tone: "safe", title: "Psst — I held that back",
        text: d.kind === "file" ? `That file for ${site} had something you marked sensitive. It never left your computer.`
          : `Your message on ${site} had something you marked sensitive. It never left — it's still in the box to edit.`,
        meta: chips, ms: 45000, open: true } };
      if (d.decision === "block") return { expr: "warn", sound: "alert", important: true, pop: {
        tone: "alert", title: "Stopped by your rule", text: `Your ${d.kind === "file" ? "file" : "message"} on ${site} matched a rule set to Block, so it didn't go.`,
        meta: chips, ms: 45000, open: true } };
      if (d.decision === "warn") return { expr: "surprised", sound: "info", pop: { tone: "info", title: "Heads up", text: `I let it through on ${site}, with a note: ${rules.join(", ") || "a warn rule matched"}.`, ms: 8000 } };
      if (d.decision === "continue") return { expr: "thumbs", sound: "info", pop: { tone: "ok", title: "Sent — your call", text: `You chose Continue on ${site}. I'll keep watching.`, ms: 6000 } };
      return null;
    }
    if (e.type === "claude.waiting") return { expr: "alert", sound: "knock", important: true, pop: { tone: "alert", title: "Claude is waiting for you", text: `Claude wants to use ${d.method || "a tool"} on ${d.server || "a server"}. Tap Allow or Block on Telegram.`, meta: ["Claude", d.server || ""], ms: 30000 } };
    if (e.type === "claude.decision") return d.allowed ? { expr: "happy", sound: null } : { expr: "protect", sound: "info", pop: { tone: "safe", title: "Blocked for you", text: `Claude's ${d.method || "tool"} on ${d.server || "a server"} didn't run.`, ms: 7000 } };
    if (e.type === "telegram.paired") return { expr: "cheer", sound: "ok", pop: { tone: "ok", title: "We're a team now", text: "I'll ask you on Telegram when I need you — even when you're away from your desk.", ms: 9000 } };
    if (e.type === "test.passed") return { expr: "thumbs", sound: "ok", pop: { tone: "ok", title: "Test passed", text: (e.title || "").replace(/^Test passed:\s*/, ""), ms: 5000 } };
    if (e.type === "extension.connected") return { expr: "happy", sound: "hello", pop: { tone: "info", title: "I can see your browser", text: "ChatGPT, Perplexity and Claude are under my watch now.", ms: 7000 } };
    if (e.type === "vault.created") return { expr: "cheer", sound: "ok", pop: { tone: "ok", title: "Vault ready", text: "Your passwords live encrypted on this computer. I'll ask before any fill.", ms: 7000 } };
    if (e.type === "vault.locked") return { expr: "sleep", sound: null };
    if (e.type === "vault.filled" || e.type === "vault.saved") return { expr: "thumbs", sound: null };
    if (e.tone === "bad" && e.title) return { expr: "sad", sound: "info", pop: { tone: "bad", title: e.title, text: e.text || "", ms: 9000 } };
    return null;
  }

  // ------------------------------------------------------------------------------- the pet
  const MOODS = {
    // Pip is never just standing there: short pauses, then the next little activity.
    calm: { gap: [1.5, 4.5], acts: { walk: 20, look: 12, guard: 9, calm: 7, play: 10, busy: 6, nap: 3, vanish: 3, beg: 3, dance: 5, wave: 4, hop: 8, spin: 4, chase: 5, stretch: 5, peek: 3 } },
    playful: { gap: [0.8, 2.5], acts: { walk: 16, look: 4, guard: 3, calm: 1, play: 18, busy: 5, nap: 1, vanish: 4, beg: 6, dance: 10, wave: 5, hop: 12, spin: 8, chase: 10, stretch: 2, peek: 5 } },
    focus: { gap: [5, 12], acts: { walk: 10, look: 30, guard: 22, calm: 16, play: 2, busy: 10, nap: 4, vanish: 1, beg: 0, dance: 0, wave: 2, hop: 2, spin: 0, chase: 0, stretch: 8, peek: 0 } },
  };

  function el(tag, attrs = {}, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) if (kid !== null && kid !== undefined && kid !== false) n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    return n;
  }

  /**
   * mount(root, {settings, adapter, filter})
   * adapter: {save(patch), openApp(), hide(), hideLabel, resize(mode), status() → text, introSeen(), markIntro()}
   */
  function mount(root, { settings = {}, adapter = {}, filter = null } = {}) {
    const sound = new Sound();
    Art.adopt(root);
    let style = null, sheet = null;
    const host = root.nodeType === 9 ? root : root.getRootNode ? root.getRootNode() : root;
    try {
      sheet = new CSSStyleSheet();
      sheet.replaceSync(CSS);
      host.adoptedStyleSheets = [...(host.adoptedStyleSheets || []), sheet];
    } catch { sheet = null; style = el("style", { text: CSS }); }

    const wrap = el("div", { class: "wb", "data-corner": "bottom-right" });
    const stage = el("div", { class: "wb-stage" });
    const fig = Art.figure("neutral", 66, true);
    const body = el("div", { class: "wb-body" }, fig);
    const pip = el("div", { class: "wb-pip", tabindex: "0", role: "button", "aria-haspopup": "dialog",
      "aria-label": "Pip, your Walled guardian. Stroke to pet, click for options." }, body);
    const fx = el("div", { class: "wb-fx" });
    stage.append(pip, fx);
    wrap.append(stage);
    if (style) root.append(style);
    root.append(wrap);

    let opts = { mood: "calm", sound: "purr", volume: 45, corner: "bottom-right", ...settings };
    let x = 18, busy = false, away = false, card = null, panel = null, dead = false, lastPointer = Date.now();
    let current = "neutral", holdUntil = 0, revertTimer = null;
    const timers = new Set();
    const later = (fn, ms) => { const t = setTimeout(() => { timers.delete(t); if (!dead) fn(); }, ms); timers.add(t); return t; };
    const maxX = () => Math.max(0, stage.clientWidth - 70);
    const side = () => (opts.corner === "bottom-left" ? "left" : "right");
    const held = () => Date.now() < holdUntil;

    function place(nx, seconds = 0) {
      x = Math.max(0, Math.min(maxX(), nx));
      pip.style.transitionDuration = `${seconds}s`;
      pip.style.left = pip.style.right = "";
      pip.style[side()] = `${x}px`;
      wrap.style.setProperty("--tail", `${Math.round(x + 26)}px`);
    }

    function apply(next = {}) {
      opts = { ...opts, ...next };
      wrap.dataset.corner = opts.corner;
      sound.set(opts);
      place(x);
    }
    apply();

    /** Show an expression; with ms it returns to the resting face afterwards. */
    function express(name, ms = 0) {
      clearTimeout(revertTimer);
      if (current !== name) { fig.set(name); current = name; }
      if (ms) revertTimer = later(() => express("neutral"), ms);
    }
    let quipEl = null;
    /** A tiny speech bubble above Pip (never over a note or the panel). */
    function quip(text, ms = 2200) {
      if (card || panel || away) return;
      if (quipEl) quipEl.remove();
      const q = el("div", { class: "wb-quip", text });
      q.style[side()] = `${Math.max(0, x - 10)}px`;
      stage.append(q); quipEl = q;
      later(() => { if (quipEl === q) { q.classList.add("leaving"); later(() => q.remove(), 260); quipEl = null; } }, ms);
    }
    function flash(cls, ms) { pip.classList.remove(cls); void pip.offsetWidth; pip.classList.add(cls); later(() => pip.classList.remove(cls), ms); }
    function eyes(dx, dy) { fig.style.setProperty("--lx", `${dx}px`); fig.style.setProperty("--ly", `${dy}px`); }

    function particle(kind, n = 1) {
      for (let i = 0; i < n; i++) {
        const p = el("i", { class: kind, text: kind === "heart" ? "♥" : "✦" });
        const base = side() === "right" ? stage.clientWidth - x - 38 : x + 28;
        p.style.left = `${base + rand(-12, 12)}px`;
        p.style.bottom = `${kind === "heart" ? 58 : 34}px`;
        const a = (i / n) * Math.PI * 2;
        p.style.setProperty("--dx", kind === "heart" ? `${rand(-14, 14)}px` : `${Math.cos(a) * 32}px`);
        p.style.setProperty("--dy", `${Math.sin(a) * -32}px`);
        fx.append(p);
        later(() => p.remove(), 1400);
      }
    }

    // --- life ------------------------------------------------------------------------
    function blinkLoop() {
      if (!away && ["neutral", "busy", "surprised", "alert", "protect", "warn"].includes(current)) {
        fig.classList.add("blink"); later(() => fig.classList.remove("blink"), 130);
        if (Math.random() < 0.2) later(() => { fig.classList.add("blink"); later(() => fig.classList.remove("blink"), 120); }, 280);
      }
      later(blinkLoop, rand(2400, 6200));
    }

    async function walkTo(nx, speed = 34) {
      const target = Math.max(0, Math.min(maxX(), nx));
      const dist = Math.abs(target - x);
      if (dist < 6) return;
      const goingOut = target > x;
      pip.classList.toggle("left", side() === "right" ? goingOut : !goingOut);
      pip.classList.add("walking");
      place(target, dist / speed);
      await wait((dist / speed) * 1000 + 60);
      pip.classList.remove("walking", "left");
    }

    const ACTIONS = {
      async walk() { await walkTo(rand(0, maxX())); },
      async look() { eyes(pick([-4, 4, -3, 3]), pick([-2, 0, 2])); await wait(rand(900, 1800)); eyes(0, 0); },
      async guard() { express("protect"); await wait(rand(2600, 4200)); if (current === "protect" && !held()) express("neutral"); },
      async calm() { express("calm"); await wait(rand(3500, 6000)); if (current === "calm" && !held()) express("neutral"); },
      async busy() { express("busy"); await wait(rand(3000, 5000)); if (current === "busy" && !held()) express("neutral"); },
      async play() {
        const tx = Math.max(0, Math.min(maxX(), x + pick([-1, 1]) * rand(40, 110)));
        const toy = el("div", { class: "wb-toy" });
        toy.style[side()] = `${tx + 26}px`;
        stage.append(toy);
        express("surprised");
        await wait(700);
        express("happy");
        await walkTo(tx);
        flash("hop", 560);
        await wait(250);
        toy.remove();
        particle("spark", 6);
        if (!held()) express("cheer", 1200);
        await wait(1200);
      },
      async nap() {
        express("sleep");
        const until = Date.now() + rand(18000, 45000);
        while (!dead && Date.now() < until && current === "sleep") await wait(1000);
        if (current === "sleep") express("calm", 1200);
      },
      async vanish() { if (!REDUCED) await goAway(rand(10000, 30000)); },
      async hop() { express("happy"); for (let i = 0; i < 2 + Math.floor(Math.random() * 2); i++) { flash("hop", 560); await wait(600); } if (current === "happy" && !held()) express("neutral"); },
      async spin() { express("cheer"); flash("spin", 900); await wait(950); particle("spark", 3); if (current === "cheer" && !held()) express("neutral"); },
      async chase() {                                   // dashes after his own spark, back and forth
        express("surprised"); await wait(250); express("happy");
        await walkTo(x < maxX() / 2 ? maxX() : 0, 110);
        await walkTo(rand(maxX() * 0.3, maxX() * 0.7), 110);
        if (current === "happy" && !held()) express("neutral");
      },
      async stretch() { express("calm"); flash("stretch", 1300); await wait(1400); if (current === "calm" && !held()) express("neutral"); },
      async peek() { if (REDUCED) return; await goAway(rand(3000, 7000)); },
      async dance() { express("cheer"); flash("dance", 2000); particle("spark", 4); await wait(2100); if (current === "cheer" && !held()) express("neutral"); },
      async wave() { express("thumbs"); quip(pick(["👋", "Still here!", "All good on my end."]), 1800); await wait(1900); if (current === "thumbs" && !held()) express("neutral"); },
      async beg(force = false) {
        // Attention-seeking: only when you're around and haven't petted him for a while.
        if (!force && (Date.now() - lastPointer > 60000 || Date.now() - lastPetAt < 4 * 60000)) return ACTIONS.look();
        begging = true;
        holdUntil = Date.now() + 12000;                 // no other activity while he waits for a pat
        express("shy"); pip.classList.add("beg"); sound.play("beg");
        quip(pick(["Pet me?", "Psst… head pats?", "I've been a good guard today…", "Just one pat?"]), 4000);
        const until = Date.now() + 12000;
        while (!dead && begging && Date.now() < until) await wait(300);
        pip.classList.remove("beg");
        if (begging) {                                  // ignored
          begging = false;
          express("sad", 2200); quip(pick(["…okay.", "Fine. I'll guard.", "Hmph."]), 2000);
        }
      },
    };

    async function goAway(ms) {
      away = true;
      closePanel();
      particle("spark", 7);
      pip.classList.add("gone");
      await wait(ms);
      if (!dead) comeBack();
    }

    function comeBack() {
      if (!away) return;
      away = false;
      place(rand(0, maxX()));
      pip.classList.remove("gone");
      express("happy", 1500);
      flash("arrive", 820);
      particle("spark", 5);
    }

    async function tick() {
      if (!busy && !away && !held() && !hovering && !card && !panel && !REDUCED && !pip.classList.contains("petting")) {
        busy = true;
        const acts = (MOODS[opts.mood] || MOODS.calm).acts;
        const total = Object.values(acts).reduce((a, b) => a + b, 0);
        let r = Math.random() * total, act = "look";
        for (const [k, v] of Object.entries(acts)) { if ((r -= v) < 0) { act = k; break; } }
        if (act === "nap" && Date.now() - lastPointer < 60000) act = "guard";     // don't doze while you're busy nearby
        try { await ACTIONS[act](); } finally { busy = false; }
      }
      const [a, b] = (MOODS[opts.mood] || MOODS.calm).gap;
      later(tick, rand(a, b) * 1000);
    }

    // --- petting -------------------------------------------------------------------------
    let stroke = null, petMs = 0, loved = false, fast = 0, down = null;
    let petStart = 0, sulkUntil = 0, begging = false, lastPetAt = Date.now(), hovering = false;
    pip.addEventListener("pointerdown", (e) => { down = { x: e.clientX, y: e.clientY }; });
    pip.addEventListener("pointerenter", () => { hovering = true; if (!pip.classList.contains("walking") && current === "neutral") eyes(0, -2); });
    pip.addEventListener("pointerenter", (e) => { stroke = { x: e.clientX, y: e.clientY, t: performance.now(), dist: 0, hearts: 0, moved: 0 }; });
    pip.addEventListener("pointermove", (e) => {
      lastPointer = Date.now();
      if (!stroke || away) return;
      const now = performance.now();
      const d = Math.hypot(e.clientX - stroke.x, e.clientY - stroke.y);
      const dt = Math.max(1, now - stroke.t);
      stroke.dist += d; stroke.x = e.clientX; stroke.y = e.clientY; stroke.t = now;
      if (stroke.dist < 18) return;
      if (Date.now() < sulkUntil) {                        // still grumpy: no pets for now
        if (!pip.classList.contains("stomp")) { flash("stomp", 1000); express("angry", 1200); quip(pick(["I said enough!", "Hmph!", "Not now."]), 1300); sound.play("grumble"); }
        return;
      }
      if (!pip.classList.contains("petting")) {
        sound.unlock();
        pip.classList.add("petting");
        petStart = Date.now();
        if (begging) { begging = false; holdUntil = 0; pip.classList.remove("beg"); loved = true; express("love"); sound.play("love"); particle("heart", 4); quip(pick(["Yay!", "Best human 💕", "Hehe, thank you!"]), 1800); }
        else express("shy");
      }
      lastPetAt = Date.now();
      if (Date.now() - petStart > 8000) return overPetted();
      petMs += dt;
      fast = d / dt > 2.4 ? fast + dt : Math.max(0, fast - dt);
      sound.pet(d, d / dt);
      stroke.hearts += d;
      if (stroke.hearts > 220) { stroke.hearts = 0; particle("heart"); }
      if (fast > 1400) { fast = 0; express("angry", 900); quip(pick(["Hey, gently!", "Easy there!", "Too fast!"]), 1200); sound.play("grumble"); later(() => { if (pip.classList.contains("petting")) express(loved ? "love" : "shy"); }, 950); }
      else if (!loved && petMs > 2600 && current !== "angry") { loved = true; express("love"); sound.play("love"); particle("heart", 3); }
      clearTimeout(stroke.idle);
      stroke.idle = setTimeout(endPet, 450);
    });
    pip.addEventListener("pointerleave", () => { hovering = false; eyes(0, 0); endPet(); });
    /** Too much of a good thing: grumpy, stomps, walks off and sulks for a few seconds. */
    async function overPetted() {
      pip.classList.remove("petting");
      sound.stopPet();
      sulkUntil = Date.now() + 7000;
      petMs = 0; loved = false;
      express("angry"); flash("stomp", 1000); sound.play("grumble");
      quip(pick(["Okay, okay — that's enough!", "Too many pats!", "I need my personal space."]), 2200);
      await wait(1100);
      if (dead || away) return;
      const far = side() === "right" ? (x > maxX() / 2 ? 0 : maxX()) : (x > maxX() / 2 ? 0 : maxX());
      express("angry");
      await walkTo(far);
      later(() => { if (current === "angry") { express("neutral"); quip("…fine, I forgive you.", 1800); } }, 5000);
    }
    function endPet() {
      if (!pip.classList.contains("petting")) return;
      petStart = 0;
      pip.classList.remove("petting");
      sound.stopPet();
      if (stroke) clearTimeout(stroke.idle);
      express(loved ? "love" : "happy", 1600);
      if (petMs > 2600) later(() => { petMs = 0; loved = false; }, 4000); else petMs = 0;
    }

    // --- click → one small panel ---------------------------------------------------------
    pip.addEventListener("click", () => {
      sound.unlock();
      const moved = down && stroke ? Math.hypot(stroke.x - down.x, stroke.y - down.y) : 0;
      if (moved > 8) return;                                     // that was a stroke
      if (card && !panel) return closeCard();
      flash("boop", 340);
      sound.play("boop");
      togglePanel();
    });
    pip.addEventListener("contextmenu", (e) => { e.preventDefault(); togglePanel(); });
    pip.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); togglePanel(); }
      if (e.key === "Escape") { closePanel(); closeCard(); }
    });

    async function sizeFor(mode) { if (adapter.resize) { try { await adapter.resize(mode); } catch { /* window gone */ } } }
    function save(patch) { if (adapter.save) Promise.resolve(adapter.save(patch)).catch(() => {}); }

    function leave(node, after) {
      node.classList.add("leaving");
      later(() => { node.remove(); if (!card && !panel) sizeFor("idle"); if (after) after(); }, 260);
    }
    function closePanel() { if (!panel) return; const p = panel; panel = null; leave(p); }
    function closeCard() { if (!card) return; const c = card; card = null; leave(c); if (!held()) express("neutral"); }
    const xButton = (fn) => el("button", { class: "wb-x", "aria-label": "Close", text: "×", onclick: fn });

    async function togglePanel() {
      if (panel) return closePanel();
      if (away) comeBack();
      if (card) { card.remove(); card = null; }
      await sizeFor("open");
      express("happy");
      const chips = (key, options) => el("div", { class: "wb-chips", role: "group" }, options.map(([v, label]) => el("button", {
        "aria-pressed": String(opts[key] === v), text: label,
        onclick: (ev) => {
          apply({ [key]: v });
          ev.currentTarget.parentElement.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", String(b === ev.currentTarget)));
          if (key === "sound" && v !== "off") { sound.unlock(); if (v === "purr") sound._purr(0.7); else [0, 1, 2].forEach((i) => later(() => sound.pet(100, 0.5), i * 160)); }
          if (key === "mood") express({ calm: "calm", playful: "cheer", focus: "protect" }[v], 1400);
          save({ [key]: v });
        } })));
      const status = await Promise.resolve(adapter.status ? adapter.status() : "").catch(() => "");
      const mini = Art.figure("happy", 44, false);
      panel = el("div", { class: "wb-card wb-panel info", role: "dialog", "aria-label": "Pip" },
        xButton(closePanel),
        el("div", { class: "wb-hello" }, mini, el("div", {}, el("b", { text: "Hi, I'm Pip" }), el("small", { text: status || "Keeping watch from this corner." }))),
        el("h5", { text: "When you pet me" }), chips("sound", [["purr", "Purr"], ["chimes", "Chimes"], ["bubbles", "Bubbles"], ["off", "Silent"]]),
        el("input", { type: "range", min: "0", max: "100", value: String(opts.volume), "aria-label": "Volume",
          onchange: (ev) => { apply({ volume: Number(ev.target.value) }); sound.play("boop"); save({ volume: Number(ev.target.value) }); } }),
        el("h5", { text: "My mood" }), chips("mood", [["calm", "Calm"], ["playful", "Playful"], ["focus", "Focus"]]),
        el("div", { class: "wb-actions" },
          adapter.openApp ? el("button", { class: "b primary", text: "Open Walled", onclick: () => { closePanel(); adapter.openApp(); } }) : null,
          el("button", { class: "b", text: "Nap 1 h", onclick: () => { closePanel(); express("sleep"); later(() => goAway(3600 * 1000), 900); } }),
          adapter.hide ? el("button", { class: "b", text: adapter.hideLabel || "Hide", onclick: () => { closePanel(); adapter.hide(); } }) : null));
      wrap.append(panel);
      const first = panel.querySelector("button[aria-pressed='true']");
      if (first) first.focus({ preventScroll: true });
    }

    // --- notes -----------------------------------------------------------------------------
    const RANK = { alert: 3, safe: 3, bad: 2, ok: 1, info: 1 };
    async function say(pop, expr = "happy") {
      const rank = RANK[pop.tone] || 1;
      const buried = () => card && Number(card.dataset.rank) > rank;   // never bury an alert under small news
      if (buried()) return null;
      closePanel();
      if (away) comeBack();
      await sizeFor("open");
      if (buried()) return null;
      if (card) { card.remove(); card = null; }
      const meta = (pop.meta || []).filter(Boolean).map((m) => (typeof m === "object" ? el("span", { class: "hot", text: m.hot }) : el("span", { text: m })));
      const actions = pop.actions || [
        { label: "Got it", primary: true, run: closeCard },
        ...(pop.open && adapter.openApp ? [{ label: "Open Walled", run: () => { closeCard(); adapter.openApp(); } }] : []),
      ];
      const node = el("div", { class: `wb-card ${pop.tone || "info"}`, role: rank >= 3 ? "alert" : "status" },
        xButton(closeCard),
        el("div", { class: "wb-note" }, Art.figure(expr, 56, true),
          el("div", {},
            el("div", { class: "wb-kicker", text: "Pip · Walled" }),
            el("h4", { text: pop.title || "" }), pop.text ? el("p", { text: pop.text }) : null,
            meta.length ? el("div", { class: "wb-meta" }, meta) : null,
            el("div", { class: "wb-actions" }, actions.map((a) => el("button", { class: `b ${a.primary ? "primary" : ""}`, text: a.label, onclick: a.run }))))));
      node.dataset.rank = String(rank);
      card = node;
      wrap.append(node);
      if (pop.ms) later(() => { if (card === node) closeCard(); }, pop.ms);
      return node;
    }

    /** React to a service event (from the app's event stream or the extension). */
    function react(e) {
      const s = story(e);
      if (!s) return null;
      const show = s.pop && (!filter || filter(e, s));
      if (away && (show || s.important)) comeBack();
      holdUntil = Date.now() + (s.important ? 6000 : 3000);
      if (s.expr === "cheer" || s.expr === "thumbs") { flash("celebrate", 1700); particle("spark", 6); }
      else if (s.expr !== "sleep") flash("hop", 560);
      express(s.expr, show ? 0 : 3000);
      if (s.sound) sound.play(s.sound);
      if (show) say(s.pop, s.expr);
      return s;
    }

    function destroy() {
      dead = true;
      timers.forEach(clearTimeout);
      sound.stopPet();
      if (style) style.remove();
      if (sheet) host.adoptedStyleSheets = host.adoptedStyleSheets.filter((x) => x !== sheet);
      wrap.remove();
    }

    place(rand(4, Math.max(8, maxX() * 0.4)));
    flash("arrive", 820);
    later(blinkLoop, 1200);
    later(tick, rand(1500, 3000));
    // First time only: say hello and show how to interact.
    later(async () => {
      const seen = adapter.introSeen ? await Promise.resolve(adapter.introSeen()).catch(() => true) : true;
      if (seen || card || dead) return;
      express("cheer");
      sound.play("hello");
      say({ tone: "info", title: "Hi, I'm Pip 👋", text: "I'll keep watch from this corner and only speak up when it matters. Stroke me to pet me — click me for options.",
        actions: [{ label: "Nice to meet you", primary: true, run: () => { closeCard(); if (adapter.markIntro) adapter.markIntro(); express("happy", 1500); } }] }, "cheer");
    }, 1600);
    if (adapter.sounds) sound.useFiles(adapter.sounds);
    return { react, say, apply, destroy, sound, el: pip, figure: fig, express, quip, closeCard, closePanel, togglePanel,
      act: (name, ...args) => (ACTIONS[name] ? ACTIONS[name](...args) : null),
      get current() { return current; }, get away() { return away; }, comeBack, goAway };
  }

  globalThis.WalledBuddy = { mount, story, Sound };
})();
