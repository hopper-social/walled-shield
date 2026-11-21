# Architecture

## 1. The problem in one paragraph

We have to stop two kinds of data, a typed prompt and an uploaded file, from leaving ChatGPT or Perplexity when they contain `WALLED_SENSITIVE`. The check has to happen **before transmission**, and the user is asked in a **native** dialog, not an HTML pop-up. Clean traffic must not be disturbed, and the two sites change their HTML often.

## 2. Options considered

| Approach | Why not (or why) |
|---|---|
| **Local HTTPS proxy** (mitmproxy-style) | Needs a root certificate installed on the machine. It breaks on certificate pinning and HTTP/3. ChatGPT uploads files straight to cloud blob storage with signed URLs, and prompts are streamed through websockets/SSE in formats that change without notice. Pulling a file back out of a multipart/chunked upload is fragile. It also cannot recolour responses. |
| **OS-level hooks / accessibility APIs** | Per-OS, privileged, and cannot see a file's bytes before the page reads them. |
| **Browser extension only** | Can see everything in the page, but cannot show a native dialog, and cannot run Tesseract or a full PDF parser well. |
| **Extension + native desktop agent** ✅ | The extension sees the action *in the page, before the site's code runs*, which is the earliest point where the data still exists in one clean piece. The agent provides what a browser cannot: an OS-drawn dialog and heavy file inspection. Chrome's native-messaging channel connects them. |

## 3. Components

```
extension/                                  src/walled_guard/
┌─────────────────────────────────┐        ┌────────────────────────────────────┐
│ page-hooks.js  (page world)     │        │ host.py      native-messaging loop │
│   hidden file inputs → DOM      │        │ inspector.py prompts / text / PDF  │
│ sites.js       selectors only   │        │ ocr.py       Tesseract + variants  │
│ content.js     intercept/replay │ stdio  │ dialogs.py   osascript · zenity ·  │
│ background.js  connectNative ───┼───────▶│              kdialog · Tk · Win32  │
│ popup/         status, colour   │  JSON  │ audit.py     content-free log      │
└─────────────────────────────────┘        │ installer.py browser registration  │
                                           └────────────────────────────────────┘
```

**Site adapters (`sites.js`)** hold everything that knows about a specific site: the hostnames, the composer, the send button, and the response containers. When a site changes its markup, this file is the only one that changes. Adding a third site (Gemini, Claude) means adding one entry here.

## 4. Interception: how a send is held

The content script registers listeners on **`window` in the capture phase**. Capture on `window` is the first stop of every DOM event, before React's root listener and before the editor's own handlers. A held event gets `preventDefault()` plus `stopImmediatePropagation()`, so the site never sees it.

| Action | Event(s) held | How it is replayed on Continue |
|---|---|---|
| Enter in composer | `keydown` (not Shift, not IME composition) | Click the send button, or re-dispatch Enter if there is no button |
| Send button | `click` (plus `pointerdown`/`mousedown` while a dialog is open) | `button.click()` |
| Form submit | `submit` | `form.requestSubmit()` |
| File picker | `input` + `change` on `<input type=file>` | Re-dispatch `input` and `change` on the same input (its `files` unchanged, or reduced to the clean ones) |
| Drag-and-drop | `drop` with files | New `DragEvent('drop')` carrying a `DataTransfer` with the approved files, at the original target and coordinates |
| Paste | `paste` with files, or very long text containing the marker | New `ClipboardEvent('paste')` carrying the approved data |

During a replay a `bypass` flag is set synchronously, because DOM dispatch is synchronous. Our own listeners therefore let the replayed event through, and nothing else can slip through in that window.

**Prompts are checked locally first.** The check is `text.includes("WALLED_SENSITIVE")`, so a clean prompt is never delayed by even a millisecond. Only a hit goes to the agent. The agent checks again (it is the authority) and shows the dialog.

**Files always go to the agent.** The marker can sit inside a PDF stream or in the pixels of a screenshot, which the page cannot see. Files are read with `FileReader` and sent as base64; the limit is 25 MB per file and 40 MB per batch. Clean files are replayed without a dialog. A short "checking…" note appears while OCR runs.

**Hidden file inputs.** Many upload libraries create an `<input type=file>` that is never attached to the page. Events on a detached element never reach `window`. `page-hooks.js` runs in the page's own JavaScript world before the site's code, and attaches such inputs to a hidden holder right before their picker opens. It also hides `window.showOpenFilePicker`, which returns files without any DOM event, so the sites fall back to a normal input. The Perplexity end-to-end test covers a detached input.

**Cancel never loses work.** A held prompt stays in the composer, so the user edits it and presses send again. Clean prompts go straight through. A held file is removed from the input (`value = ""`), or the input is reduced to the clean files from the same batch.

**Edits during the dialog.** If the composer text differs from what was approved, it is not sent and the user is told to press send again. This stops someone from approving one text and sending another.

## 5. Inspection (agent)

| Type | Detection | Matching |
|---|---|---|
| Prompt | — | Exact, case-sensitive |
| Text file | Magic bytes, then extension/MIME, then a "looks textual" heuristic | UTF-8/BOM/UTF-16/Latin-1 decode; exact match or line-break tolerant |
| PDF | `%PDF` | ① pypdf text per page (reports *page N*) ② raw content streams, Flate-decoded (catches split runs, form fields, metadata) ③ OCR of image-only pages |
| Image | PNG/JPEG/GIF/WebP/BMP/TIFF/HEIC magic | ① metadata bytes (EXIF/XMP/PNG text) ② Tesseract on up to 3 variants (contrast-stretched, inverted for dark mode, binarised), upscaled to 1400 px, page modes 3 and 11; stops at the first hit |

OCR matching folds look-alikes (`0→O`, `1/l/|→I`, `5/$→S`, and `L≡I`) and ignores a misread underscore. It errs toward asking, because a false dialog costs one click and a miss leaks data. Decompression bombs are refused through Pillow's pixel limit, and Tesseract calls time out after 20 s.

## 6. The native dialog

| OS | Backend | Notes |
|---|---|---|
| macOS | `osascript` → `display dialog` | Caution icon. Default button and Esc are both **Cancel**. Gives up after 120 s, which counts as Cancel. |
| Windows | Tk window (topmost), falling back to Win32 `MessageBoxW` | Cancel focused; closing the window counts as Cancel |
| Linux | zenity → kdialog → xmessage → Tk | `--default-cancel`. With xmessage, a timeout exits 0, so Continue is mapped to exit code 10. |

The dialog states whether the hit was a **prompt** or a **file**. For a prompt it shows an excerpt centred on the marker; for files it shows each file name and where the marker was found. If no backend can be shown, the answer is Cancel and the reason goes to stderr, which Chrome's log captures. `walled-guard status` and the popup both report "NONE".

## 7. Native messaging and trust

- **Wire format:** a 4-byte little-endian length followed by UTF-8 JSON. Every request carries an `id` that is echoed back.
- **Connections:** one connection per request. Chrome starts the agent, which handles one request and exits when the port closes. A crash affects one request, never the browser, and an open native port keeps the MV3 service worker alive while the dialog is up.
- **Who may connect:** `allowed_origins` names exactly one extension ID. That ID comes from the public key in `manifest.json`, so it is identical on every machine and a different extension cannot talk to the agent.
- **Where the host is registered:**
  - macOS/Linux: per-user host manifests in each browser's `NativeMessagingHosts` folder;
  - Windows: `HKCU\Software\<Browser>\NativeMessagingHosts`.
- **The launcher:** a wrapper script runs the virtualenv's Python. It also restores the PATH that browsers strip, so Homebrew's Tesseract is found.

## 8. Failure handling ("fail closed")

| Failure | Behaviour |
|---|---|
| Agent missing or crashing, or extension reloaded mid-page | Marked prompts and all uploads are **blocked** with an on-page notice. Clean prompts still work because they are checked locally. |
| Agent exception | Returns `{ok:false}`, which counts as blocked. The bridge keeps serving the next request. |
| File not checkable (damaged or encrypted PDF, over 25 MB, image without OCR) | Listed in the dialog as "not checked"; the user decides |
| Unsupported type (zip, docx, video) | Passes through, since it is outside scope |
| Timeout, Esc, or window closed | Cancel |

## 9. Response recolouring

The content script injects a `<style>` at `document_start`, built from the adapter's `responses` selectors:

- ChatGPT: `[data-message-author-role="assistant"]`
- Perplexity: `[id^="markdown-content-"]`, `.prose`

It colours every descendant with `!important`, but skips code blocks and math so syntax highlighting stays readable. Links stay underlined. The colour is a CSS variable, so a change made in the popup applies to open tabs instantly through `chrome.storage.onChanged`. User messages are left alone.

## 10. Privacy

- File bytes stay in memory on the local machine, inside the agent process that inspects them, and are discarded afterwards.
- Nothing is sent to any server.
- The decision log stores only the time, site, prompt-or-file, decision and a count; it keeps the last 1000 lines.

## 11. Hardening for a company rollout

- Force-install the extension and block disabling it: `ExtensionInstallForcelist`, and `ExtensionSettings` with `"installation_mode": "force_installed"`.
- Deploy the agent with MDM.
- Block other browsers and incognito: `IncognitoModeAvailability=1`.
- Turn the marker into a policy: the inspector is a single function, so regexes, document fingerprints or classifiers can replace `WALLED_SENSITIVE` without touching the browser side.

## 12. Testing

- **Unit (68 tests):**
  - matching rules, encodings, file-type detection;
  - every sample file;
  - corrupt and oversized input;
  - the protocol's framing and failure paths;
  - dialog command construction (quoting, default-cancel, the xmessage timeout trap);
  - installer registration and removal;
  - the host accepting the arguments Chrome appends (origin, `--parent-window`);
  - `doctor` launching the installed host like Chrome does.
- **End to end (18 tests):**
  - real Chrome for Testing with the unpacked extension, through real native messaging, to the real agent started by the launcher that `walled-guard install` writes;
  - stand-in pages served at the real ChatGPT and Perplexity URLs;
  - covers clean prompts with no dialog; Cancel then edit and retry; Continue sends text unchanged; the send button; the agent being down; clean, flagged and mixed uploads; drag-and-drop; pasted screenshots; a detached file input; recolouring on both sites; and a live colour change from the popup.
- **Static:** `mypy --strict` and `node --check`.
