# Walled for organisations: architecture, guardrails and operations

This document is for security, IT and compliance teams evaluating Walled. It covers:

- what runs where;
- what data exists and where it goes;
- which guardrails are enforced, and how they fail;
- how to deploy and operate it at scale.

Two companion documents go deeper: the threat-by-threat defences are in [SECURITY.md](SECURITY.md), and the review history with regression tests is in [SECURITY-REVIEW.md](SECURITY-REVIEW.md).

> Pip, the on-screen companion, is a presentation layer only. He reads the same event stream as the app and has no privileges of his own: he cannot change policy, approve anything or see content.

---

## 1. Architecture

```
 Browser (Chrome / Edge / Brave / Arc)            This computer                                 Phone
┌────────────────────────────────────┐   native  ┌──────────────────────────────────────────┐  HTTPS  ┌──────────────┐
│ Walled extension (MV3)             │ messaging │ walled-guard host (per request, stdio)   │         │ Telegram bot │
│  • intercepts prompt / upload      │──────────▶│  • inspects text, PDF, DOCX, images/OCR  │         │ (user-owned) │
│    before the page sends it        │◀──────────│  • shows the native OS dialog            │         └──────▲───────┘
│  • local pre-check (same rules)    │           └──────────────┬───────────────────────────┘                │
│  • password helper (opt-in)        │                          │ Unix socket (0600)                         │
└────────────────────────────────────┘           ┌──────────────▼───────────────────────────┐  Bot API      │
 Claude Desktop ── MCP stdio ──▶ mcp-approval-gate│ walled service (login item)              │───────────────┘
                   proxy (per server)  ─────────▶│  • policy engine · approval daemon       │
                                                 │  • encrypted vault · event bus · audit   │
                                                 │  • Control Center on 127.0.0.1 (random)  │
                                                 └──────────────────────────────────────────┘
```

| Component | Runs as | Lifetime | Talks to |
|---|---|---|---|
| Browser extension | Chrome MV3 service worker + content scripts on ChatGPT, Perplexity, Claude.ai (Telegram Web only during guided pairing) | Browser session | Native host only (`nativeMessaging`). No remote servers. |
| `walled-guard` native host | The user, launched by Chrome for each request | One request | Local files, OS dialog, service socket |
| `walled` service | The user, launchd / systemd user unit | Login → logout | Unix socket, loopback HTTP, Telegram Bot API |
| MCP approval gate | The user, wraps each guarded MCP server in Claude Desktop | Per Claude session | Service socket |
| Control Center (app) | Loopback web UI in a native window | On demand | Service over `127.0.0.1` with a session token |
| Telegram bot | Owned by the user (created via BotFather) | — | Only the paired chat |

Everything runs **unprivileged as the logged-in user**. Nothing needs root, kernel extensions, a VPN, TLS interception or a proxy.

## 2. Data inventory

| Data | Where it lives | Leaves the device? | Retention |
|---|---|---|---|
| Prompt text, file contents, tool inputs | Memory only, during inspection | **Never** | Discarded after the decision |
| Policies (rules) and settings | `config.json` in the private state folder (0600) | No | Until changed |
| Audit log: time, site, kind, decision, rule *names*, channel | `decisions.jsonl` (0600) + MCP activity SQLite | Only if the user connects a Telegram log channel, as masked one-liners | Rolling; exportable as CSV / JSON |
| Masked samples, e.g. `•••• 4242` | Telegram approval/alert cards | Yes, to the user's own bot | Telegram chat history (user-controlled) |
| Vault (passwords) | `vault.json`: AES-256-GCM, scrypt-derived key, or OS keychain | **Never** (fills are local; approvals carry only site + masked username) | Until deleted |
| Bot token, chat ID | Private state folder (0600) | Used only to call the Telegram Bot API | Until unpaired |

State folder: macOS `~/Library/Application Support/WalledGuard/` · Linux `~/.local/state/walled-guard/`.

## 3. Guardrails

| # | Guardrail | Enforcement |
|---|---|---|
| G1 | **Inspect before transmission** | Capture-phase listeners intercept Enter, send buttons, file inputs, drag-and-drop, paste and `showOpenFilePicker` before site code runs. The original event is replayed only after an explicit Continue. |
| G2 | **Local-only inspection** | Text, PDF (per page, raw-stream fallback), DOCX, UTF-16 and images via local Tesseract OCR. No cloud classifier. |
| G3 | **Human in the loop** | Native OS dialog (NSAlert / zenity / kdialog / Win32), which a web page can't draw or click. Cancel is the default button. |
| G4 | **Fail closed** | Host unavailable → uploads and matching prompts are blocked. Dialog unavailable → Cancel. No answer before the timeout (default 120 s) → Cancel / Block. Telegram unreachable → MCP denied. |
| G5 | **Graduated policy** | Each rule is Ask, Warn or Block. Phone mode: off, alert, or the phone can answer. Built-in detectors use checksums (Luhn, Verhoeff) to cut false positives. Regexes are screened for catastrophic backtracking and scans are capped at 2 MB. |
| G6 | **Out-of-band approval for agents** | Each Claude MCP tool call waits for Allow / Block from the paired Telegram account. Sensitive inputs are hidden on the card, can't be "always allowed", and Block rules deny without asking. |
| G7 | **Bounded approval sessions** | Approvals expire after 24 h and need a code shown on the computer to renew. `/pairout` revokes at once. Callbacks are checked for user, chat, message and a 128-bit nonce. |
| G8 | **Minimum disclosure** | Telegram only ever receives rule names, file names, site names and masked samples. Passwords never leave the device. |
| G9 | **Local surface hardening** | Loopback-only server on a random port with a Host check against DNS rebinding. Admin token in a 0600 file. One-time 60 s launch tickets. Strict CSP with `frame-ancestors` limited to Walled's extension. No CORS. Private 0700/0600 files and socket. |
| G10 | **Origin pinning** | The password helper uses the tab's real origin (`sender.origin`), https only. Its UI is in a closed shadow root and ignores untrusted (synthetic) clicks. |
| G11 | **Content-free audit** | Every decision is logged without content. CSV exports neutralise spreadsheet formulas. |
| G12 | **Self-verification** | 12 end-to-end checks in the app (Verification) pass only on observed behaviour. `walled doctor` checks every component the way the browser launches it. |

## 4. Deployment at scale

**Install**
- Ship the wheel or the installer bundle with MDM (Jamf, Kandji, Intune for macOS).
- Run `bash install.sh` in the user context. It's non-interactive, logs to `~/.walled/install.log`, and exits non-zero on failure.
- The unattended equivalent from a mounted share: `python3 -m venv ~/.walled/venv && ~/.walled/venv/bin/pip install walled_shield-*.whl && ~/.walled/venv/bin/walled install --no-open`.

**Extension**
- Publish privately to the Chrome Web Store, or host the CRX.
- Force-install with `ExtensionInstallForcelist` / `ExtensionSettings` (`installation_mode: "force_installed"`). The extension ID is fixed by the manifest key: `lbnpgknepeocidimhgmodenkcfngmkdj`.
- Recommended alongside: `IncognitoModeAvailability = 1`, and block the AI sites in browsers you don't manage.

**Baseline policy**
- Pre-seed `config.json` in the state folder with organisation terms: client names, project code names, file patterns, folders.
- Use **Block** for anything that must never leave.
- Users can add their own rules on top.

**Telegram**
- Each user pairs their own bot (about 1 minute, guided).
- For teams, an *activity log channel* can receive masked one-line decisions for review.

**Remove:** run `walled uninstall --purge --yes`, then remove the extension policy.

## 5. Operations

| Need | How |
|---|---|
| Health check | `walled status` (exit code), `walled doctor` (each component, with the fix) |
| Logs | `service.log` (rotating, 3 × 1 MB) in the state folder; `install.log` |
| Audit export | App → Audit log → **Export CSV / JSON**, or `GET /api/activity/export?format=csv` on the loopback API (session token) |
| SIEM / review | Forward the JSON export, or subscribe a team channel to the masked Telegram log |
| Verify after changes | App → Verification (dialog, Telegram, race, per-site prompt and file checks) |
| Updates | Replace the wheel and re-run `walled install`. The extension keeps its ID; press ↻ on unpacked installs. |

## 6. Mapping to common frameworks

Walled is a technical control. It *supports* these requirements; it does not certify compliance on its own.

| Framework | Requirement area | How Walled contributes |
|---|---|---|
| India DPDP Act 2023 | Reasonable security safeguards; purpose limitation for personal data | Detects and holds Aadhaar, PAN, phone and email before disclosure to third-party AI; no new processing of the content |
| GDPR | Art. 5(1)(f) integrity & confidentiality; Art. 32 security of processing | Prevents accidental transfer of personal data to AI processors; content-free audit trail |
| ISO/IEC 27001:2022 | A.5.12 classification, A.5.14 information transfer, A.8.12 data leakage prevention | Policy-based detection, user-confirmed transfer, logged decisions |
| SOC 2 | CC6.1 logical access, CC6.7 transmission restriction, CC7.2 monitoring | Approval gating for AI tool actions, transmission restriction, exportable audit |

## 7. Known limits

- **Scope:** ChatGPT, Perplexity and Claude.ai in Chromium browsers, plus Claude Desktop MCP. Other AI apps and browsers aren't covered yet.
- **Same-user attacker:** a user, or malware running as them, can edit local policy or disable components. Use MDM to force-install the extension and keep the login item in place.
- **Site redesigns:** site selectors live in one file (`src/walled/extension/src/sites.js`) and are covered by end-to-end tests. A redesign can need an update.
- **No central server:** policy is per device in this version. A managed-policy endpoint is the natural next step for fleets.
