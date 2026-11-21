"use strict";
// The Walled app inside Chrome's side panel: no switching between the browser and a window.
// The panel asks the local Walled service (through the desktop agent) for a one-time ticket
// and loads the app with it. Nothing here talks to the internet.
const $ = (id) => document.getElementById(id);

async function open() {
  $("loading").hidden = false; $("offline").hidden = true; $("app").hidden = true;
  const reply = await chrome.runtime.sendMessage({ type: "agent", payload: { type: "ticket" } }).catch(() => null);
  if (!reply || !reply.ok) { $("loading").hidden = true; $("offline").hidden = false; return; }
  const frame = $("app");
  frame.src = `${reply.url}#k=${encodeURIComponent(reply.ticket)}&compact=1`;
  frame.onload = () => { $("loading").hidden = true; frame.hidden = false; };
}

$("retry").addEventListener("click", open);
open();
