# Security model

## What Walled defends against

| Threat | Defence |
|---|---|
| Sensitive text or files pasted into ChatGPT or Perplexity by mistake | Interception before the site's code runs; native dialog; rules with checksums; OCR for images and scans |
| A web page faking the confirmation | The dialog is drawn by the OS (osascript / zenity / Win32), not HTML |
| A Claude MCP tool doing something you didn't intend | Every call waits for Allow on Telegram. Calls containing sensitive data hide their inputs, cannot be always-allowed, and can be blocked outright by rule (A1 model in [mcp-gate/SECURITY.md](mcp-gate/SECURITY.md)). |
| Someone with your Telegram account approving things | 24 h session; `/pairout`; renewal needs a code shown on the computer; callbacks are checked for user, chat, message ID and a 128-bit nonce |
| Telegram (the service or a breach) reading your data | Only masked samples, rule names and file names are sent. Prompt text, file contents and passwords never leave the machine. |
| Another local app or web page driving the Control Center | Loopback only; admin token kept in a 0600 file; windows open with one-time 60-second launch tickets, so no secret appears on a command line; Host check against DNS rebinding; no CORS; strict CSP |
| Another local user | Private 0700 state folders, 0600 files and socket (A1 `paths.py`) |
| A page tricking the password helper | The fill site comes from the tab's real origin (`sender.origin`), never the page. https only. The chip sits in a closed shadow root and ignores untrusted clicks. Fills can require a Telegram tap. |
| A stolen vault file | AES-256-GCM over the whole file. scrypt key derivation (N=2^15, r=8, p=1, random salt). Wrong passwords fail authentication, with growing delays after 5 failures. |
| A stolen unlocked laptop | Vault auto-locks after inactivity (default 15 min) and locks on `/lock` from Telegram or when the service stops |

## Fail-closed behaviour

| Situation | Result |
|---|---|
| Native host missing or crashing | Marked prompts and all uploads are blocked. Clean prompts work, because they are checked locally. |
| Service not running | The browser guard still asks with the desktop dialog. MCP approvals are denied. Vault requests fail. |
| No dialog program | Cancel, with a warning in the app, the popup and `doctor` |
| Telegram unreachable, session off, or no tap in time | MCP calls are denied. Browser questions fall back to the desktop answer. Fills are denied. |
| Unreadable file (encrypted or damaged PDF, over 25 MB, image without OCR) | Listed as "not checked" in the dialog; you decide |

## Deliberate trade-offs

- **Rules are local JSON** (0600). A user can edit or disable them, and so can malware running as that user. Walled protects you from mistakes and from misbehaving AI tools; it does not protect the machine from its own administrator.
- **Telegram is optional.** Without it, every password fill is confirmed with a native desktop dialog, unless the user explicitly chooses "No extra check".
- **The extension can be disabled.** For a company rollout:
  - force-install it with `ExtensionInstallForcelist` / `ExtensionSettings` (`installation_mode: force_installed`);
  - disable incognito with `IncognitoModeAvailability=1`;
  - deploy the agent with MDM.
- **Not a full password manager:** there is no sync, sharing, passkeys or breach monitoring. The vault exists so that sign-ups get strong unique passwords and logins can be approved from your phone.

See [SECURITY-REVIEW.md](SECURITY-REVIEW.md) for the v2.1 review: findings, fixes and the regression tests that cover them.

## Reporting

Please report security issues privately to the maintainer rather than in a public issue.
