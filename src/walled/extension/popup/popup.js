"use strict";

const PRESETS = ["#d9480f", "#2563eb", "#16a34a", "#9333ea", "#db2777"];
const $ = (id) => document.getElementById(id);

async function loadSettings() {
  const s = await chrome.storage.sync.get({ recolor: true, color: "#d9480f", passwordHelper: false });
  $("recolor").checked = s.recolor;
  $("pwhelper").checked = s.passwordHelper && await chrome.permissions.contains({ origins: ["<all_urls>"] });
  $("color").value = s.color;
  renderSwatches(s.color);
}

function renderSwatches(active) {
  const box = $("swatches");
  box.replaceChildren(...PRESETS.map((c) => {
    const b = document.createElement("button");
    b.style.background = c;
    b.title = c;
    b.setAttribute("aria-label", `Use ${c}`);
    b.setAttribute("aria-pressed", String(c.toLowerCase() === active.toLowerCase()));
    b.addEventListener("click", () => setColor(c));
    return b;
  }));
}

async function setColor(color) {
  $("color").value = color;
  renderSwatches(color);
  await chrome.storage.sync.set({ color, recolor: true });
  $("recolor").checked = true;
}

async function checkAgent() {
  const reply = await chrome.runtime.sendMessage({ type: "agent", payload: { type: "ping" } }).catch((e) => ({ ok: false, error: String(e) }));
  const dot = $("dot");
  if (!reply || !reply.ok) {
    dot.className = "dot bad";
    $("agent-title").textContent = "Desktop agent not found";
    $("agent-detail").textContent = `Chrome says: “${(reply && reply.error) || "no reply"}”. Sends containing the marker are blocked until this is fixed. Run “walled-guard doctor” in a terminal to see the exact cause.`;
    return;
  }
  const noDialog = String(reply.dialog || "").startsWith("NONE");
  dot.className = reply.ocr && !noDialog ? "dot ok" : "dot warn";
  $("agent-title").textContent = `Protected · agent ${reply.version}`;
  if (reply.service !== undefined) {
    $("pw-detail").textContent = reply.service
      ? `Walled app running${reply.telegram ? " · Telegram on" : ""}.` : "Open the Walled app to use the vault.";
  }
  const notes = [];
  if (noDialog) notes.push("No dialog program was found, so every flagged send is cancelled. Install zenity.");
  notes.push(reply.ocr
    ? `Dialogs: ${reply.dialog}. Images and scanned PDFs are read with OCR.`
    : "Tesseract is not installed, so text inside images and scanned PDFs cannot be read. Each image upload will ask you before it is sent.");
  $("agent-detail").textContent = notes.join(" ");
}

$("recolor").addEventListener("change", (e) => chrome.storage.sync.set({ recolor: e.target.checked }));
$("pwhelper").addEventListener("change", async (e) => {
  let enabled = e.target.checked;
  // The permission prompt must come straight from the click, here in the popup.
  if (enabled) enabled = await chrome.permissions.request({ origins: ["<all_urls>"] });
  else await chrome.permissions.remove({ origins: ["<all_urls>"] }).catch(() => {});
  const reply = await chrome.runtime.sendMessage({ type: "password-helper", enabled });
  e.target.checked = !!(reply && reply.ok && reply.enabled);
});
$("color").addEventListener("input", (e) => setColor(e.target.value));
loadSettings();
checkAgent();

$("panel").addEventListener("click", async () => {
  const win = await chrome.windows.getCurrent();
  await chrome.sidePanel.open({ windowId: win.id });
  window.close();
});
