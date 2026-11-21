# Security review: v2.1 and v2.2

This is a full pass over every surface of the merged product. Each finding below was fixed and has a regression test (`tests/platform/test_v21.py`, unless noted otherwise).

## Findings fixed in this release

| # | Severity | Surface | Finding | Fix |
|---|---|---|---|---|
| 1 | **High** | Control Center launcher | The Chrome app-window fallback put the admin token on the command line (`--app=…#t=TOKEN`). On a shared computer, any local user can read the process list and then call the loopback API, which can reveal vault passwords. | The launcher asks the service for a **one-time launch ticket** (single use, 60 s) and passes only that. The page exchanges it in a POST body for a window session token. The admin token never leaves its 0600 file. |
| 2 | **High** | Vault over the socket | When Telegram was not set up, any program running as the user could ask the socket for a password from an unlocked vault, with no second check. | Fills now require confirmation: a Telegram tap, or a **native desktop dialog** when Telegram is off. Skipping confirmation requires the explicit "No extra check" setting. |
| 3 | Medium | Browser agent | A PDF containing a Flate "zip bomb" stream could inflate to gigabytes in memory while the raw streams were scanned. | Each stream is capped at 16 MB and each file at 64 MB of decompressed data. |
| 4 | Medium | Rule engine | A user regex with nested quantifiers, such as `(a+)+`, could backtrack catastrophically on a long prompt or file and freeze the agent. Python's `re` has no timeout. | Such patterns are rejected when the rule is saved, and custom regexes scan at most the first 2 MB of a text. |
| 5 | Medium | State folder | The Walled state folder was created with the default umask (often 0755), and the decision log with 0644. Other local users could read rule names and sites. | The folder is created as 0700 (a looser folder we own is tightened), and the log is opened as 0600. |
| 6 | Low | Native host launcher | The user's PATH was copied into the launcher as-is. An empty or relative entry (`.`) would let a file in Chrome's working folder shadow a real program. Paths were also double-quoted, so `$` or `"` in a path would break. | Only absolute, existing directories are kept, and every value is single-quoted with proper escaping. |
| 7 | Low | Native host launcher | Chrome appends the caller's origin (and `--parent-window` on Windows). The CLI rejected these arguments, so the host exited immediately and looked like "agent not found". *(Fixed in 1.0.1 and kept in a test here.)* | The `host` subcommand ignores the browser's arguments. |
| 8 | Low | Windows/Linux Tk dialog | A local variable named `cancel` (a Tk button) shadowed the cancellation event, so the dialog/phone race would crash the Tk dialog. Found by `mypy --strict`. | Renamed, with a test for cancelling a dialog program. |

## v2.6: new surfaces, reviewed

- **`/api/activity/export`:** session-token protected and read-only. It exports the same content-free fields as the Audit log. CSV cells that start with `= + - @` are prefixed with `'` to prevent spreadsheet formula injection. Served with `Content-Disposition: attachment` and `nosniff`.
- **`/sounds/*`:**
  - file names must match `[a-z]+.(mp3|ogg|wav|m4a)`, so there is no path traversal;
  - it reads only from the private state folder and the packaged folder;
  - `index.json` lists names only.
- **Extension `web_accessible_resources`:** limited to `sounds/*` on the five guarded origins.

## v2.4: new surfaces, reviewed

- **Desktop Pip window:**
  - It loads only the loopback Control Center with a one-time ticket, the same as the app.
  - Its JS bridge exposes four methods: resize to two fixed sizes, open the app, reconnect (a fresh ticket from the 0600 control file) and hide. Nothing takes a path or a command.
  - The window does not take focus.
- **`/api/pet/*`** sits behind the session token; `show` and `hide` only start or stop `walled pet`.
- **Browser Pip:**
  - Event text from the service is set with `textContent`; the SVG is static.
  - Local preferences live in `chrome.storage.local`.
  - The shadow root is open, but it holds no secrets (rule names and site only, as in the companion cards).
- **Events:** `claude.waiting` and `claude.decision` carry the server and method names only, never tool inputs.

## v2.3: new surfaces, reviewed

- **`/api/browser/extensions`:** it sits behind the same session token as every Control Center route. It copies only the extension folder path, which is not secret, and launches a browser by fixed executable name with a constant argument (`chrome://extensions`). No request data reaches a command line.
- **Pip:**
  - Pip's SVG is a static string set with `innerHTML`; no event or user data is interpolated.
  - Speech text is written with `textContent`.
  - The companion's mini Pip is static markup inside the existing shadow root.
- **Installer:**
  - It no longer prompts, so it is safe to run unattended.
  - `.command` launchers only `cd` to their own folder and run `install.sh`.
  - Optional Homebrew work runs in the background and can't fail the install.

## v2.2: new surfaces, reviewed

| Surface | Decision |
|---|---|
| **Telegram Web assistant** (`telegram.js` on web.telegram.org) | It reads the page only to detect the login state, BotFather's replies and a bot token, and only while you are in the setup flow. Every message it types is sent by a trusted click on a Walled card. A token goes only to the local service (native messaging → Unix socket), never to a website. Nothing else is read or sent. |
| **Companion cards** (open shadow root) | They show titles and masked details only, never prompt text, files, passwords or tokens. Buttons ignore untrusted clicks, so a page cannot press them. The password chip keeps its *closed* shadow root because it displays a generated password. |
| **Event bus and live channel** | Events carry titles, rule names and masked details. The long-poll uses the private 0600 socket; the extension receives events through native messaging, and only Walled's extension ID can connect. Old events are not replayed to pages after a restart (an age check of more than 120 s). |
| **Side panel framing** | The app may be framed only by Walled's own extension: `frame-ancestors 'self' chrome-extension://lbnpgknepeocidimhgmodenkcfngmkdj`. The panel gets a one-time ticket through the native host, exactly like the desktop window. |
| **Activity log channel** | Uses A1's alert-channel pairing, extended to accept the `startgroup` deep link. Messages are masked, silent and `protect_content`. The approvals chat never receives the feed. |
| **Keychain vault** | The random key is written with `security -i`, with commands on stdin, so it never appears in a process list. Reading it back is gated by macOS (it may prompt for the login password). |
| **Clipboard guard** | On copy it reads only the current selection, matched locally against secret patterns. The clipboard is overwritten after 60 s unless you choose *Keep it*. |
| **Password-manager capture** | Secret fields are not `type=password` and carry `autocomplete=off` plus the opt-out hints that 1Password, LastPass and Bitwarden recognise. The app window's Chrome profile disables password saving and autofill. |
| **Test center practice pages and samples** | Static, served from the package with path-traversal checks. They need no token and contain nothing secret. |

## Reviewed and found sound

- **Control Center API:**
  - binds to loopback only, on a random port;
  - Host-header check stops DNS rebinding;
  - no CORS, and the custom auth header forces a preflight that is never approved;
  - strict CSP (`default-src 'self'`, no inline script or style attributes);
  - the UI builds the DOM with `textContent` only;
  - request bodies are capped at 1 MB;
  - settings are clamped, and rules are validated on the server.
- **Telegram:**
  - the A1 model is unchanged: per-request 128-bit nonces, paired user, chat and message checks, a 24 h session, `/pairout`, and renewal only through a code shown on the computer;
  - deep links (`t.me/<bot>?start=CODE`) carry the same one-time code, so they are no weaker than typing it;
  - cards contain only masked samples, rule names, file names and masked account names.
- **Extension:**
  - vault requests take the site from `sender.origin` (never from the page), are refused on non-https pages, and are dropped if they come from anything but a tab;
  - the password chip lives in a closed shadow root and ignores untrusted clicks (end-to-end test: a page script cannot press it);
  - there is no `externally_connectable`, so web pages cannot message the extension;
  - all-sites access is optional and requested only when the user turns the helper on.
- **Native messaging:** `allowed_origins` is pinned to one extension ID derived from the manifest key; messages are capped at 64 MB; failures are fail-closed.
- **Vault:**
  - AES-256-GCM over the whole file, with an scrypt key (N=2^15, r=8, p=1, 16-byte salt);
  - atomic 0600 writes;
  - the key is held only in memory and dropped on lock, auto-lock, `/lock` or shutdown;
  - unlock attempts slow down after 5 failures.
- **MCP gate (A1):** the previous review still applies ([mcp-gate/SECURITY.md](mcp-gate/SECURITY.md)). The new rule hook can only *tighten* a decision: it hides inputs, removes *Always allow*, or blocks. It never auto-allows.

## Accepted risks (documented for users)

- **Same-user malware** can read the 0600 files and use the socket like the user can. Walled protects against mistakes and misbehaving AI tools, not against a compromised account. Mitigations: fills need confirmation, the vault auto-locks, and `/lock` works from the phone.
- **A user can disable the extension.** Managed rollouts should force-install it by policy (see [SECURITY.md](SECURITY.md)).
- **Telegram is a third party.** Only masked metadata is ever sent, and approvals can be turned off at any time.
