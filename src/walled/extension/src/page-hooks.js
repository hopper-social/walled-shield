/*
 * Runs in the page's own JavaScript world (manifest "world": "MAIN"), before the site's code.
 * Two small adjustments so every file the user picks passes through a <input type=file>
 * whose events the content script can see:
 *
 *  1. A file input that is not attached to the document (a common trick in upload
 *     libraries) is attached to a hidden holder before its picker opens, so its "change"
 *     event reaches window, where the content script listens.
 *  2. The File System Access picker (window.showOpenFilePicker) returns files without any
 *     DOM event. It is hidden on these two sites, so they use their regular file input.
 */
(() => {
  "use strict";

  const holderId = "walled-guard-file-inputs";
  function attach(input) {
    if (input.isConnected) return;
    let holder = document.getElementById(holderId);
    if (!holder) {
      holder = document.createElement("div");
      holder.id = holderId;
      holder.hidden = true;
      (document.body || document.documentElement).appendChild(holder);
    }
    holder.appendChild(input);
  }

  const proto = HTMLInputElement.prototype;
  const originalClick = proto.click;
  proto.click = function click(...args) {
    if (this.type === "file") attach(this);
    return originalClick.apply(this, args);
  };
  if (typeof proto.showPicker === "function") {
    const originalShowPicker = proto.showPicker;
    proto.showPicker = function showPicker(...args) {
      if (this.type === "file") attach(this);
      return originalShowPicker.apply(this, args);
    };
  }

  try {
    delete Window.prototype.showOpenFilePicker;   // the API lives on the prototype
    delete window.showOpenFilePicker;
  } catch { /* not supported in this browser: nothing to hide */ }
})();
