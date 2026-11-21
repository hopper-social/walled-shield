/* Pip's look: one vector character with 16 expressions, shared by every place Pip appears
 * (desktop corner, browser corner, notes, the app, the dock, the browser cards).
 *
 * Drawn after the "AI Guard Assistant" character sheet, in flat colour (no gradients): wide helmet head with ear pods and an
 * orange antenna, a dark glossy screen for a face with glowing cyan eyes, a small floating body
 * with an orange shield. "Protecting" is green.
 *
 * An expression is a set of data attributes (eyes, mouth, pose, marks, aura) on the wrapper;
 * the CSS shows the matching parts. So one SVG animates between all expressions.
 */
(() => {
  "use strict";
  if (globalThis.PipArt) return;

  /** name: [eyes, mouth, pose, marks, aura, label] */
  const EXPR = {
    neutral:   ["open", "smile", "down", "", "", "Here"],
    happy:     ["happy", "smile", "down", "rays", "", "All good!"],
    thumbs:    ["wink", "smile", "thumbs", "star", "", "Great!"],
    love:      ["love", "smile", "heart", "", "", "You're awesome!"],
    calm:      ["calm", "smile", "pray", "", "orange", "Relax"],
    alert:     ["stern", "firm", "fists", "bang", "", "Attention!"],
    protect:   ["stern", "firm", "shield", "", "green", "Stay safe!"],
    confused:  ["rings", "wavy", "scratch", "ask", "", "Let me check…"],
    think:     ["squint", "flat", "chin", "ask", "", "Analyzing…"],
    sleep:     ["calm", "none", "pillow", "zzz", "", "Resting…"],
    sad:       ["sad", "frown", "droop", "broken", "", "It's okay…"],
    angry:     ["angry", "frown", "fists", "puff", "red", "Stop it!"],
    shy:       ["happy", "wavy", "down", "hearts", "", "Aww!"],
    surprised: ["wide", "o", "out", "bangs", "", "Oh!"],
    cheer:     ["happy", "smile", "up", "rays", "", "You got this!"],
    warn:      ["stern", "firm", "stop", "", "", "Be careful!"],
    busy:      ["down", "flat", "laptop", "", "blue", "Working…"],
  };

  const CY = "#4ee8ff";
  const SVG = (u) => `
<svg viewBox="0 0 120 124" class="pa-svg" aria-hidden="true" focusable="false">

  <ellipse class="pa-shadow" cx="60" cy="120" rx="22" ry="3.6"/>
  <g class="pa-aura"><ellipse cx="60" cy="100" rx="47" ry="10"/><ellipse class="pa-aura2" cx="60" cy="100" rx="38" ry="7"/></g>
  <g class="pa-fig">
    <g class="p-pillow"><path d="M14 108 C10 98 26 92 60 93 C94 92 110 98 106 108 C102 117 18 117 14 108 Z" fill="#4762c8"/><path d="M22 104 C40 99 80 99 98 104" stroke="#7d95ef" stroke-width="2" fill="none" stroke-linecap="round"/></g>
    <!-- body -->
    <path class="pa-body" d="M40 80 C37 95 45 109 60 113 C75 109 83 95 80 80 Z" fill="#262d4e"/>
    <path d="M47 107 C53 111.5 67 111.5 73 107" stroke="#f2a33a" stroke-width="2.6" fill="none" stroke-linecap="round"/>
    <g class="pa-arm pa-armL"><rect x="33" y="82" width="9" height="17" rx="4.5" fill="#262d4e" stroke="#323b62" stroke-width="1"/><circle cx="37.5" cy="99" r="4.6" fill="#f2a33a"/></g>
    <g class="pa-arm pa-armR"><rect x="78" y="82" width="9" height="17" rx="4.5" fill="#262d4e" stroke="#323b62" stroke-width="1"/><circle cx="82.5" cy="99" r="4.6" fill="#f2a33a"/></g>
    <g class="pa-badge"><path d="M60 84 l12 4.5 v8 c0 9 -5.5 13.5 -12 16.5 c-6.5 -3 -12 -7.5 -12 -16.5 v-8 z" fill="#f2a33a" stroke="#f8c56e" stroke-width="1.2"/>
      <path d="M54.5 97.5 l4 4 l7.5 -8.5" stroke="#fff6dc" stroke-width="2.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></g>
    <!-- head -->
    <g class="pa-head">
      <rect x="8" y="38" width="14" height="28" rx="7" fill="#1a2242" stroke="#f2a33a" stroke-width="2.6"/>
      <rect x="98" y="38" width="14" height="28" rx="7" fill="#1a2242" stroke="#f2a33a" stroke-width="2.6"/>
      <path d="M62 16 C62 10 64 7 67 5" stroke="#323b62" stroke-width="3.6" fill="none" stroke-linecap="round"/>
      <circle class="pa-glow" cx="68" cy="4.5" r="9"/>
      <circle class="pa-ball" cx="68" cy="4.5" r="5.2" fill="#f6b552"/>
      <rect x="14" y="14" width="92" height="70" rx="31" fill="#2c3459" stroke="#5b66a8" stroke-opacity=".55" stroke-width="1.2"/>
      <path d="M18 60 C22 76 38 84 60 84 C82 84 98 76 102 60" stroke="#f2a33a" stroke-width="2.4" fill="none" opacity=".9"/>
      <path d="M30 22 C44 15 76 15 90 22" stroke="#fff" stroke-opacity=".2" stroke-width="3" fill="none" stroke-linecap="round"/>
      <rect x="22" y="25" width="76" height="52" rx="23" fill="#070a14" stroke="#0a0d1c" stroke-width="2"/>
      <path d="M30 34 C35 29 44 27.5 54 27.5" stroke="#fff" stroke-opacity=".12" stroke-width="3" fill="none" stroke-linecap="round"/>
      <g class="pa-face">
        <g class="pa-eyes">
          <g class="e e-open"><rect x="40" y="43" width="11" height="15" rx="5.5"/><rect x="69" y="43" width="11" height="15" rx="5.5"/><circle class="sh" cx="48" cy="46.5" r="1.8"/><circle class="sh" cx="77" cy="46.5" r="1.8"/></g>
          <g class="e e-down"><rect x="40" y="47" width="11" height="13" rx="5.5"/><rect x="69" y="47" width="11" height="13" rx="5.5"/></g>
          <g class="e e-wide"><circle cx="45.5" cy="50" r="7.5"/><circle cx="74.5" cy="50" r="7.5"/><circle class="sh" cx="48" cy="47" r="2"/><circle class="sh" cx="77" cy="47" r="2"/></g>
          <path class="e e-happy st" d="M38 54 Q45.5 44 53 54 M67 54 Q74.5 44 82 54"/>
          <path class="e e-calm st" d="M38 49 Q45.5 56 53 49 M67 49 Q74.5 56 82 49"/>
          <g class="e e-wink"><circle class="st" cx="45.5" cy="50" r="6"/><path class="st" d="M81 44.5 L70 50 L81 55.5"/></g>
          <path class="e e-love pink" d="M45.5 58 l-7.4 -7.4 a4.4 4.4 0 0 1 7.4 -5.8 a4.4 4.4 0 0 1 7.4 5.8 z M74.5 58 l-7.4 -7.4 a4.4 4.4 0 0 1 7.4 -5.8 a4.4 4.4 0 0 1 7.4 5.8 z"/>
          <path class="e e-stern" d="M38 45 L53 49.5 L53 54 Q45.5 60 38 54 Z M82 45 L67 49.5 L67 54 Q74.5 60 82 54 Z"/>
          <path class="e e-angry red" d="M38 44 L53 50 L53 54 Q45.5 59.5 38 54 Z M82 44 L67 50 L67 54 Q74.5 59.5 82 54 Z"/>
          <g class="e e-rings"><circle class="st" cx="45" cy="51" r="7"/><circle class="st" cx="75" cy="51" r="5"/><path class="st" d="M69 40 Q75 37 81 41"/></g>
          <g class="e e-squint"><path d="M38 49 L53 51.5 L53 54 Q45.5 58 38 54 Z"/><rect x="69" y="41" width="11" height="15" rx="5.5"/></g>
          <g class="e e-sad"><circle class="st" cx="45.5" cy="52" r="6"/><circle class="st" cx="74.5" cy="52" r="6"/><path class="st" d="M38 43 L51 40.5 M82 43 L69 40.5"/><path class="tear" d="M80 59 q2.5 4 0 6.5 q-2.5 -2.5 0 -6.5 z"/></g>
        </g>
        <ellipse class="pa-blush" cx="36" cy="62" rx="5.5" ry="3"/><ellipse class="pa-blush" cx="84" cy="62" rx="5.5" ry="3"/>
        <g class="pa-mouth">
          <path class="m m-smile st" d="M54 64 Q60 69.5 66 64"/>
          <path class="m m-firm st" d="M55 66 Q60 63.5 65 66"/>
          <path class="m m-frown st" d="M54 67 Q60 62.5 66 67"/>
          <path class="m m-wavy st" d="M53 65.5 q3.5 -2.6 7 0 q3.5 2.6 7 0"/>
          <path class="m m-flat st" d="M55.5 65.5 L64.5 65.5"/>
          <ellipse class="m m-o" cx="60" cy="66" rx="3" ry="3.4"/>
        </g>
      </g>
    </g>
    <!-- poses with their own hands and props -->
    <g class="p-thumbs"><rect x="84" y="76" width="9" height="16" rx="4.5" fill="#262d4e" stroke="#323b62" transform="rotate(-50 86 86)"/><rect x="95" y="70" width="10" height="10" rx="5" fill="#f2a33a"/><rect x="98" y="61" width="4.4" height="11" rx="2.2" fill="#f2a33a"/></g>
    <g class="p-cheer"><path d="M41 88 L20 75 M79 88 L100 75" stroke="#2a3260" stroke-width="9" stroke-linecap="round"/><circle cx="17" cy="72" r="5.4" fill="#f2a33a"/><circle cx="103" cy="72" r="5.4" fill="#f2a33a"/></g>
    <g class="p-heart"><circle cx="49" cy="98" r="4.6" fill="#f2a33a"/><circle cx="71" cy="98" r="4.6" fill="#f2a33a"/>
      <path d="M60 110 l-11 -11 a6.4 6.4 0 0 1 11 -8.6 a6.4 6.4 0 0 1 11 8.6 z" fill="#f6b552" class="glowy"/></g>
    <g class="p-pray"><path d="M60 85 C54 89 53 98 54.5 105 L60 105 Z" fill="#f2a33a"/><path d="M60 85 C66 89 67 98 65.5 105 L60 105 Z" fill="#f2a33a"/><path d="M60 87 L60 104" stroke="#c4501a" stroke-width="1" stroke-opacity=".6"/></g>
    <g class="p-shield"><path d="M84 78 l17 6.4 v11 c0 12.6 -7.6 19 -17 23.6 c-9.4 -4.6 -17 -11 -17 -23.6 v-11 z" fill="#2fbf86" stroke="#c8ffe0" stroke-width="1.4" class="glowg"/>
      <path d="M76.5 96.5 l5.5 5.5 l10 -11.5" stroke="#f2fff8" stroke-width="3.2" fill="none" stroke-linecap="round" stroke-linejoin="round"/></g>
    <g class="p-scratch"><rect x="84" y="66" width="9" height="20" rx="4.5" fill="#262d4e" stroke="#323b62" transform="rotate(-30 88 84)"/><circle cx="96" cy="66" r="5.2" fill="#f2a33a"/></g>
    <g class="p-chin"><rect x="72" y="80" width="9" height="14" rx="4.5" fill="#262d4e" stroke="#323b62" transform="rotate(35 76 86)"/><circle cx="70" cy="81" r="4.8" fill="#f2a33a"/></g>
    <g class="p-stop"><circle cx="24" cy="80" r="5" fill="#f2a33a"/><rect x="88" y="88" width="3" height="20" fill="#9aa3b8"/>
      <path d="M83.5 66 h11 l7.8 7.8 v11 l-7.8 7.8 h-11 l-7.8 -7.8 v-11 z" fill="#e5383b" stroke="#fff" stroke-width="1.8"/>
      <text x="89" y="82.5" text-anchor="middle" font-family="Arial Black,Arial,sans-serif" font-weight="900" font-size="7.4" fill="#fff">STOP</text></g>
    <g class="p-laptop"><path d="M72 96 L108 90 L104 112 L68 118 Z" fill="#3aa0ff" fill-opacity=".22" stroke="#7cc4ff" stroke-width="1.3" class="glowb"/>
      <path d="M76 101 L100 97 M75 106 L95 102.6 M74 111 L98 107" stroke="#9fd6ff" stroke-width="1.4" stroke-linecap="round"/><circle cx="70" cy="100" r="4.4" fill="#f2a33a"/></g>
  </g>
  <!-- marks around him -->
  <g class="k k-rays"><path d="M14 18 l-4 -6 M22 12 l-1.5 -7.5 M8 27 l-7 -2.5" stroke="#ffc24d" stroke-width="3" stroke-linecap="round"/></g>
  <g class="k k-star"><path d="M106 12 l2.6 5.6 6 .7 -4.5 4 1.3 6 -5.4 -3.1 -5.4 3.1 1.3 -6 -4.5 -4 6 -.7 z" fill="#ffd23f"/></g>
  <g class="k k-bang"><rect x="102" y="6" width="5.5" height="16" rx="2.7" fill="#ff4d5e"/><circle cx="104.7" cy="27" r="3" fill="#ff4d5e"/></g>
  <g class="k k-bangs"><rect x="6" y="4" width="5" height="15" rx="2.5" fill="#ffb020" transform="rotate(-14 8 12)"/><circle cx="11" cy="23" r="2.7" fill="#ffb020"/><rect x="15" y="2" width="5" height="15" rx="2.5" fill="#ffb020" transform="rotate(-8 17 9)"/><circle cx="19.5" cy="21" r="2.7" fill="#ffb020"/></g>
  <text class="k k-ask" x="104" y="20" font-family="system-ui,sans-serif" font-weight="900" font-size="20" fill="${CY}">?</text>
  <g class="k k-zzz"><text x="96" y="22" font-family="system-ui,sans-serif" font-weight="900" font-size="15" fill="#8fa8ff">Z</text><text x="108" y="12" font-family="system-ui,sans-serif" font-weight="900" font-size="10" fill="#8fa8ff">z</text></g>
  <g class="k k-hearts"><path d="M11 22 l-5 -5 a3 3 0 0 1 5 -4 a3 3 0 0 1 5 4 z" fill="#ff6bb0"/><path d="M108 34 l-3.6 -3.6 a2.2 2.2 0 0 1 3.6 -2.9 a2.2 2.2 0 0 1 3.6 2.9 z" fill="#ff6bb0"/></g>
  <g class="k k-broken"><path d="M104 22 l-7 -7 a4.2 4.2 0 0 1 7 -5.5 l-2 4 3 2 -1 6.5 z" fill="#ff5c7a"/><path d="M106 21 l1 -6 -3 -2 2 -3.5 a4.2 4.2 0 0 1 7 5.5 z" fill="#ff5c7a"/></g>
  <g class="k k-puff"><circle cx="11" cy="14" r="6" fill="#ff4d5e"/><circle cx="18" cy="9" r="5" fill="#ff4d5e"/><circle cx="19" cy="17" r="5" fill="#ff4d5e"/></g>
</svg>`;

  const CSS = `
  .pa{display:inline-block;position:relative;width:var(--pa-size,60px);aspect-ratio:120/124;line-height:0;vertical-align:middle}
  .pa-svg{width:100%;height:100%;overflow:visible}
  .pa-svg *{transform-box:view-box}
  .pa .pa-shadow{fill:rgba(5,10,20,.24)}
  .pa .pa-fig{transform-origin:60px 118px}
  .pa.pa-live .pa-fig{animation:pa-float 3.6s ease-in-out infinite}
  .pa.pa-live .pa-shadow{transform-origin:60px 120px;animation:pa-shadow 3.6s ease-in-out infinite}
  .pa .pa-glow{fill:#f2a33a;opacity:.35;transform-origin:68px 4.5px}
  .pa.pa-live .pa-glow{animation:pa-glow 2.6s ease-in-out infinite}
  .pa .pa-eyes{transform-origin:60px 51px;transition:transform .25s cubic-bezier(.3,.7,.3,1)}
  .pa .pa-eyes .e{fill:${CY};filter:drop-shadow(0 0 3px rgba(78,232,255,.85))}
  .pa .pa-eyes .st,.pa .pa-mouth .st{fill:none;stroke:${CY};stroke-width:4;stroke-linecap:round;stroke-linejoin:round}
  .pa .pa-mouth .st{stroke-width:2.6}
  .pa .pa-mouth{filter:drop-shadow(0 0 2px rgba(78,232,255,.7))}
  .pa .m-o{fill:${CY}}
  .pa .sh{fill:#fff;opacity:.9}
  .pa .pink{fill:#ff5c8a!important;filter:drop-shadow(0 0 4px rgba(255,92,138,.85))!important}
  .pa .red{fill:#ff4d5e!important;filter:drop-shadow(0 0 4px rgba(255,77,94,.9))!important}
  .pa .tear{fill:#8fd8ff}
  .pa .pa-blush{fill:#ff6b9a;opacity:0;transition:opacity .3s}
  .pa .glowy{filter:drop-shadow(0 0 5px rgba(242,163,58,.8))}.pa .glowg{filter:drop-shadow(0 0 6px rgba(47,191,134,.8))}.pa .glowb{filter:drop-shadow(0 0 5px rgba(80,170,255,.8))}
  .pa .e,.pa .m,.pa .k,.pa [class^="p-"],.pa .pa-aura{display:none}
  .pa[data-eyes="open"] .e-open,.pa[data-eyes="down"] .e-down,.pa[data-eyes="wide"] .e-wide,.pa[data-eyes="happy"] .e-happy,
  .pa[data-eyes="calm"] .e-calm,.pa[data-eyes="wink"] .e-wink,.pa[data-eyes="love"] .e-love,.pa[data-eyes="stern"] .e-stern,
  .pa[data-eyes="angry"] .e-angry,.pa[data-eyes="rings"] .e-rings,.pa[data-eyes="squint"] .e-squint,.pa[data-eyes="sad"] .e-sad{display:inline}
  .pa[data-mouth="smile"] .m-smile,.pa[data-mouth="firm"] .m-firm,.pa[data-mouth="frown"] .m-frown,.pa[data-mouth="wavy"] .m-wavy,
  .pa[data-mouth="flat"] .m-flat,.pa[data-mouth="o"] .m-o{display:inline}
  .pa[data-marks~="rays"] .k-rays,.pa[data-marks~="star"] .k-star,.pa[data-marks~="bang"] .k-bang,.pa[data-marks~="bangs"] .k-bangs,
  .pa[data-marks~="ask"] .k-ask,.pa[data-marks~="zzz"] .k-zzz,.pa[data-marks~="hearts"] .k-hearts,.pa[data-marks~="broken"] .k-broken,
  .pa[data-marks~="puff"] .k-puff{display:inline}
  .pa[data-blush] .pa-blush,.pa[data-eyes="love"] .pa-blush{opacity:.65}
  .pa[data-aura] .pa-aura{display:inline;fill:none;stroke:var(--aura);stroke-width:2.2;opacity:.9;filter:drop-shadow(0 0 5px var(--aura))}
  .pa .pa-aura2{opacity:.45}
  .pa[data-aura="orange"]{--aura:#f2a33a}.pa[data-aura="green"]{--aura:#2fbf86}.pa[data-aura="red"]{--aura:#ff4d5e}.pa[data-aura="blue"]{--aura:#4aa8ff}
  .pa.pa-live[data-aura] .pa-aura{transform-origin:60px 100px;animation:pa-ring 2.8s ease-in-out infinite}
  /* poses */
  .pa .pa-arm{transition:transform .35s cubic-bezier(.3,1.3,.5,1)}
  .pa .pa-armL{transform-origin:37.5px 84px;transform:rotate(18deg)}.pa .pa-armR{transform-origin:82.5px 84px;transform:rotate(-18deg)}
  .pa[data-pose="up"] .pa-arm{display:none}.pa[data-pose="up"] .p-cheer{display:inline}
  .pa[data-pose="out"] .pa-armL{transform:rotate(70deg)}.pa[data-pose="out"] .pa-armR{transform:rotate(-70deg)}
  .pa[data-pose="fists"] .pa-armL{transform:rotate(40deg)}.pa[data-pose="fists"] .pa-armR{transform:rotate(-40deg)}
  .pa[data-pose="droop"] .pa-armL{transform:rotate(4deg)}.pa[data-pose="droop"] .pa-armR{transform:rotate(-4deg)}
  .pa[data-pose="thumbs"] .pa-armR,.pa[data-pose="heart"] .pa-arm,.pa[data-pose="pray"] .pa-arm,.pa[data-pose="shield"] .pa-armR,
  .pa[data-pose="scratch"] .pa-armR,.pa[data-pose="chin"] .pa-armR,.pa[data-pose="stop"] .pa-arm,.pa[data-pose="laptop"] .pa-armR{display:none}
  .pa[data-pose="heart"] .pa-badge,.pa[data-pose="pray"] .pa-badge,.pa[data-pose="shield"] .pa-badge{display:none}
  .pa[data-pose="thumbs"] .p-thumbs,.pa[data-pose="heart"] .p-heart,.pa[data-pose="pray"] .p-pray,.pa[data-pose="shield"] .p-shield,
  .pa[data-pose="scratch"] .p-scratch,.pa[data-pose="chin"] .p-chin,.pa[data-pose="stop"] .p-stop,.pa[data-pose="laptop"] .p-laptop,
  .pa[data-pose="pillow"] .p-pillow{display:inline}
  .pa[data-pose="pillow"] .pa-head{transform:rotate(-8deg) translate(-2px,6px);transform-origin:60px 80px}
  .pa[data-pose="pillow"] .pa-arm{display:none}
  .pa[data-pose="chin"] .pa-head{transform:rotate(-5deg);transform-origin:60px 80px}
  .pa[data-pose="droop"] .pa-head{transform:translateY(2px) rotate(3deg);transform-origin:60px 80px}
  .pa .pa-head{transition:transform .35s ease}
  .pa.blink .pa-eyes{transform:scaleY(.1)}
  .pa.swap .pa-face{animation:pa-swap .28s ease-out}
  @keyframes pa-float{50%{transform:translateY(-3.5px)}}
  @keyframes pa-shadow{50%{transform:scale(.86);opacity:.7}}
  @keyframes pa-glow{50%{transform:scale(1.35);opacity:.15}}
  @keyframes pa-ring{50%{transform:scale(1.05,1.12);opacity:.55}}
  @keyframes pa-swap{0%{transform:scale(.92);opacity:.4}100%{transform:none;opacity:1}}
  @media (prefers-reduced-motion: reduce){.pa *{animation:none!important;transition:none!important}}
  `;

  let count = 0;
  const attrs = (name) => {
    const [eyes, mouth, pose, marks, aura] = EXPR[name] || EXPR.neutral;
    return { eyes, mouth, pose, marks, aura, blush: name === "shy" || name === "love" ? "1" : "" };
  };

  /** Static markup for an expression (safe: only fixed names and numbers go in). */
  function markup(name = "neutral", { size = 60, live = false } = {}) {
    const a = attrs(EXPR[name] ? name : "neutral");
    const data = Object.entries(a).filter(([, v]) => v).map(([k, v]) => ` data-${k}="${v}"`).join("");
    return `<span class="pa${live ? " pa-live" : ""}"${data}>${SVG(`pa${++count}`)}</span>`;     // size: set --pa-size in CSS
  }

  /** Switch an existing figure to another expression (with a tiny pop). */
  function apply(el, name) {
    const a = attrs(EXPR[name] ? name : "neutral");
    for (const k of ["eyes", "mouth", "pose", "marks", "aura", "blush"]) {
      if (a[k]) el.dataset[k] = a[k]; else delete el.dataset[k];
    }
    el.dataset.expr = name;
    el.classList.remove("swap"); void el.offsetWidth; el.classList.add("swap");
  }

  /** A live figure element: figure("happy", 64).set("love"). */
  function figure(name = "neutral", size = 60, live = true) {
    const holder = document.createElement("span");
    holder.innerHTML = markup(name, { size, live });       // static markup, fixed names only
    const el = holder.firstElementChild;
    el.dataset.expr = name;
    el.style.setProperty("--pa-size", `${Number(size) || 60}px`);    // CSSOM: allowed under strict CSP
    el.set = (n) => apply(el, n);
    return el;
  }

  /** Put the CSS where it works under strict CSPs (constructed sheet), else a <style>. */
  const adopted = new WeakSet();
  function adopt(root = document) {
    const host = root.nodeType === 9 ? root : root.getRootNode ? root.getRootNode() : root;
    if (adopted.has(host)) return;
    adopted.add(host);
    try {
      const sheet = new CSSStyleSheet();
      sheet.replaceSync(CSS);
      host.adoptedStyleSheets = [...(host.adoptedStyleSheets || []), sheet];
    } catch {
      const style = document.createElement("style");
      style.textContent = CSS;
      (host.head || host).append(style);
    }
  }

  globalThis.PipArt = { EXPR, CSS, markup, figure, apply, adopt, names: Object.keys(EXPR) };
})();
