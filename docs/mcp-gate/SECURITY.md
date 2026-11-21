# Security

## What the gate protects

**Invariant:** a request that does something (`tools/call`, `resources/read`, `prompts/get`, or any method not on the read-only allowlist) is forwarded to an MCP server **only** after a positive decision for *that exact request* from the paired human. Every other outcome (timeout, error, cancellation, outage, revoked session, overload) returns JSON-RPC error `-32001` to Claude, and the server never sees the request.

Automatically forwarded (unless `--strict`): `initialize`, `ping`, `tools/list`, `resources/list`, `resources/templates/list`, `prompts/list`. Responses and `notifications/*` are relayed without approval.

## Threat model

| Threat | Mitigation |
| --- | --- |
| Claude (or a prompt injection steering it) calls a dangerous tool | Held until a human sees tool, server and arguments and taps Allow |
| A stranger finds the bot and taps buttons or sends `/pair` | Only the paired user ID in the paired private chat is honoured; group/channel/bot messages are ignored; renewals need a code shown on the computer |
| Replay or cross-request approval (tap on an old or different card) | 128-bit nonce per request bound to its message ID; nonce discarded once decided, expired or cancelled |
| Approval after Claude abandoned the request | Cancellation withdraws the prompt; late taps find no pending request |
| Lost phone / compromised Telegram account | `/pairout` from the paired chat on another device signed into your Telegram account, or `mcp-approval-gate uninstall --purge` locally and `/revoke` in @BotFather; sessions expire after 24 h regardless |
| Secrets leaking into Telegram | Values under credential-like keys, known token formats, `user:password@` in URLs, `?token=`-style query parameters and `password=`/`Authorization:` text are redacted (22 forms covered by tests); `--hide-inputs` for metadata-only; `--approver popup` keeps data local |
| Secrets leaking into Claude's config, process lists or logs | Bot token read from a `0600` file by the launcher and passed via environment; activity log stores no arguments; gate secrets stripped from the MCP server's environment |
| Another local user requesting or forging decisions | Socket `0600` in a `0700` state folder owned by the user; state files checked for owner, mode and symlinks |
| Gate down → silent bypass | Wrapped servers start only through the gate; if the daemon is unreachable they refuse to start or deny every action |
| Resource exhaustion (flooding requests or connections) | Bounded queues and semaphores everywhere (128 daemon connections, 32 waiting prompts); overflow is denied, not queued |
| An argument too large or too HTML-heavy to display | The preview is cut on the *escaped* text so the card always fits Telegram's limit and is delivered, marked *Preview shortened*; `--deny-truncated` blocks such requests instead |
| Ambiguous JSON that parses differently in gate and server | Duplicate keys, non-finite numbers, oversized integers, lone surrogates and batches are rejected before approval |
| Malicious remote upstream (HTTP mode) | Redirects refused; client credentials never forwarded upstream |

## Service hardening

The background service runs with `UMask=0077` (launchd `Umask 63`) so every file it creates is private. On Linux the systemd user unit also sets `NoNewPrivileges`, `LockPersonality`, `RestrictSUIDSGID`, `RestrictRealtime` and `SystemCallArchitectures=native`. These are options that work in unprivileged user services; mount-namespace options such as `ProtectSystem` are deliberately not used, because they make the unit fail to start on systems without unprivileged user namespaces.

## Out of scope

- **Traffic that never touches your machine:** Anthropic-hosted connectors, Claude's built-in tools (web search, file upload, artifacts).
- **A malicious MCP server.** The gate decides whether a request reaches the server; it does not sandbox the server, which runs as your user and can read whatever you can.
- **A compromised user account on the computer.** Anyone running as you can read the state folder or edit Claude's config.
- **Telegram itself.** Bot messages are encrypted in transit to Telegram but not end-to-end; Telegram can read card contents.
- **Claude's and macOS's own permission prompts** are separate and unaffected (`mcp-approval-gate permissions`).

## Operational guidance

- Use a **dedicated bot** for the gate; never share its token with another program (a second poller also breaks approvals).
- Keep the 24 h session; use `/pairout` when you'll be unreachable so requests fail fast instead of timing out.
- Use *Always allow* only for read-only or low-impact tools; review with `/rules`, reset with `/clear`.
- Run `mcp-approval-gate status` after adding servers; anything **UNGUARDED** bypasses the gate.
- For Claude Web, put HTTP mode behind an authenticated HTTPS gateway; never expose the loopback listener directly.

## Reporting a vulnerability

Please open a private security advisory on the repository (GitHub → Security → Report a vulnerability) rather than a public issue.
