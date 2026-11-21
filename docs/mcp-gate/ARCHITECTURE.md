# Architecture

MCP Approval Gate is a **man-in-the-middle proxy that the user installs on purpose**. It sits between an MCP client (Claude Desktop, or Claude Web via a gateway) and an MCP server, inspects every JSON-RPC message, and lets an action through only after a human explicitly allows it.

## 1. System context

```text
                    ┌──────────────────────────── user's computer ─────────────────────────────┐
                    │                                                                         │
 Claude Desktop ──stdio──► [ stdio proxy ] ──stdio──► real MCP server (npx …, docker …, python …)│
 Claude Desktop ──stdio──► [ stdio proxy ] ──stdio──► real MCP server                           │
                    │            │                                                            │
 Claude Web ─HTTPS─► gateway ─► [ http proxy ] ──HTTPS──► remote MCP server                     │
                    │            │                                                            │
                    │            ▼  approve? (Unix socket, 0600)                              │
                    │      [ approval daemon ] ◄──────── long-poll ────────► Telegram Bot API ─┼──► 📱 paired chat
                    │            │                                                            │
                    │      private state folder (0700): bot token, pairing, session,          │
                    │      daemon lock, socket, activity.sqlite3, daemon.log                  │
                    └─────────────────────────────────────────────────────────────────────────┘
```

Claude Desktop starts each configured MCP server as a child process and talks newline-delimited JSON-RPC over its stdin/stdout. `wrap-desktop` rewrites each entry so Desktop starts **the gate** instead, and the gate starts the real server as *its* child. Claude cannot reach the original server any other way.

## 2. Components

All code lives in the `mcp_approval_gate` package (`src/mcp_approval_gate/`). Standard library only.

| Module | Responsibility |
| --- | --- |
| `gate.py` | **The decision contract.** Classifies each client message; auto-forwards setup/listing methods (`initialize`, `ping`, `*/list`); holds everything else for `decide(request) -> bool`. Bounded to 32 concurrent decisions. Exceptions, cancellation and audit failures all deny. Rejects ambiguous JSON (duplicate keys, non-finite numbers, huge integers, lone surrogates) and never forwards batches. |
| `stdio_proxy.py` | Launches the real server, relays server→client lines verbatim, routes client→server lines through the gate on a 32-worker pool. Tracks outstanding IDs, honours `notifications/cancelled`, rejects duplicate IDs, fails every outstanding request if the child exits, strips the gate's own secrets from the child's environment. |
| `http_proxy.py` | Streamable HTTP reverse proxy for remote servers. Constant-time bearer check on inbound requests, strips hop-by-hop and client auth headers, optional separate upstream token, refuses redirects, streams SSE, cancels the approval if the client disconnects. Binds to loopback only. |
| `approval_daemon.py` | **The single Telegram owner.** Listens on a private Unix socket; for each `approve` request it renders a card, asks the paired chat, and answers allow/deny. Holds the in-memory *Always allow* rules, handles `/status /rules /clear`, and renews the 24 h session without restarting. |
| `daemon_client.py` | Proxy side of the socket. `DaemonApprover.decide()` blocks until the daemon replies, watches the request's cancellation flag, and closes the socket to withdraw a prompt. Can autostart the daemon. Every error is a denial. |
| `telegram_approver.py` | Bot API client and decision engine: long-polls `getUpdates`, sends prompts with per-request 128-bit callback nonces, validates every tap (user, private chat, message ID, nonce), edits the message with the outcome, handles `/pairout`. Also the original one-process-per-server mode. |
| `telegram_session.py` | The 24 h approval session file: private, owner-only, non-symlink, bound to the paired chat and user. Missing, corrupt, expired or world-readable means *inactive*. |
| `telegram_launcher.py` | Loads the bot token and pairing from private files and `exec`s the CLI with them in the environment, so secrets never appear in `claude_desktop_config.json` or on a command line. |
| `request_format.py` | Turns a request into the Telegram card. Redacts credential-like keys and known token formats; flags truncated previews. |
| `popup.py` | Optional local approver: a topmost Tk window, all Tk calls on the main thread, one window at a time. Requests too large to preview in full are blocked, not partially shown. |
| `desktop_config.py` | Reads and writes `claude_desktop_config.json`: wrap, unwrap, status. Backup before every write, atomic replace, idempotent, upgrades entries wrapped by older versions. |
| `service.py` | Generates and loads the launchd agent / systemd user unit (`KeepAlive` / `Restart=always`). |
| `cli_admin.py` | User-facing commands: `quickstart`, `setup`, `test`, `renew`, `status`, `wrap-desktop`, `uninstall`. |
| `main.py` | Argument parsing and process wiring for every mode. |
| `activity.py` | Last 1,000 outcomes in a private SQLite file: time, server, method, tool, outcome. Never arguments. |
| `paths.py` | Per-OS private state folder, permission checks, atomic `0600` writes. |
| `examples/` | A real MCP notes server (and optional HTML reader) used by tests, the demo and first-run checks. |

## 3. Request lifecycle (stdio, Telegram)

```text
Claude            stdio proxy                 gate                daemon                 Telegram / human
  │ tools/call id=7 ─►│                          │                    │                         │
  │                   │ register id 7 (cancel flag)                   │                         │
  │                   │ evaluate ───────────────►│ not a control method                         │
  │                   │                          │ decide() ──socket──►│ rule? no                │
  │                   │                          │                    │ render card, sendMessage ─►│ 🔐 card
  │                   │                          │                    │        … waits …         │
  │                   │                          │                    │◄── callback "a:<nonce>" ──│ taps Allow
  │                   │                          │                    │ verify user/chat/msg/nonce│
  │                   │                          │◄── {"decision":"allow"} │ edit card ✅ Allowed  ─►│
  │                   │◄─ forward ───────────────│ audit "allowed"    │                         │
  │                   │ write to child stdin ───► real server         │                         │
  │◄──── result id=7 ─│◄── child stdout ─────────                     │                         │
```

**Block / timeout / error:** the gate answers Claude locally with `{"jsonrpc":"2.0","id":7,"error":{"code":-32001,"message":"MCP request blocked or approval unavailable"}}`; the server never sees the request.

**Cancellation:** if Claude sends `notifications/cancelled` for id 7 while it waits, the proxy sets the flag → `DaemonApprover` closes its socket → the daemon sees EOF, withdraws the prompt (card edited to *Cancelled by Claude*) → the request is dropped. A late tap finds no pending nonce and is ignored.

## 4. Concurrency model

| Where | Mechanism | Bound |
| --- | --- | --- |
| stdio proxy | reader threads + `ThreadPoolExecutor` | 32 workers, 64 queued lines, 256 outstanding IDs |
| gate | `BoundedSemaphore` per gate | 32 decisions waiting; more are denied, not queued |
| daemon | threading Unix-socket server; per-connection watcher thread | 128 open connections, 32 prompts waiting (`DaemonConfig.max_pending`) |
| Telegram | one long-poll thread; pending map guarded by a lock; per-request `Event` | one message per request |
| http proxy | threading server | 64 sockets / 64 active requests, 75 s idle timeout |
| popup | Tk main thread only; workers wait on one-slot queues | one window at a time |

`initialize` is processed inline (not on the pool) so the handshake order is preserved; everything else is decided in parallel, which is why parallel tool calls each get their own prompt.

## 5. The daemon protocol

Newline-delimited JSON over `<state>/approvals.sock`, one request/response per connection, 1 MiB limit, 10 s to send the request.

```text
→ {"op":"approve","server":"filesystem","method":"tools/call","params":{"name":"write_file","arguments":{…}}}
← {"decision":"allow","reason":"allow"}        reason ∈ allow | always | auto | deny | expired | cancelled |
                                                         session ended | busy | too large | invalid request
→ {"op":"status"}                ← {"ok":true,"healthy":true,"session_active":true,"session_expires_at":…,"pending":0,"rules":1}
→ {"op":"renew","code":"<random>"}  ← {"ok":true}   (resolves when the paired user sends /pair <code>)
```

Closing the socket before the reply cancels the prompt. Unknown ops and malformed messages get a denial.

### Why a daemon at all

Telegram allows **one** `getUpdates` consumer per bot; a second poller makes the first fail with HTTP 409. Claude Desktop starts every MCP server simultaneously, so one-poller-per-proxy breaks as soon as two servers are guarded. The daemon is the only poller, and an `flock` on `<state>/daemon.lock` guarantees there is never more than one daemon. Proxies started with `--autostart` (what `wrap-desktop` writes) spawn it on demand, so approvals work even if the service was never installed.

## 6. Process supervision

```text
login ─► launchd / systemd --user ─► python -m mcp_approval_gate.telegram_launcher daemon
                                         │ loads token + pairing from 0600 files
                                         └─ exec python -m mcp_approval_gate daemon   (secrets only in env)
crash ─► restarted after 5–10 s           SIGTERM ─► pending prompts denied, socket removed
```

## 7. Failure handling

| Failure | Behaviour |
| --- | --- |
| Human doesn't answer within the timeout (120 s daemon, 60 s popup) | Denied; card stamped *No answer — blocked* |
| Telegram unreachable / polling errors | Pending requests denied; polling retried every few seconds; new prompts that can't be delivered are denied |
| Daemon not running | Guarded server refuses to start (or autostarts the daemon); any in-flight decide() is denied |
| Session expired or `/pairout` | Every action denied; rate-limited notice tells the user to run `renew` |
| More than 32 prompts waiting | Extra requests denied immediately |
| MCP server crashes | Every outstanding request gets an error; pending approvals cancelled |
| Claude cancels / disconnects | Approval withdrawn, request never forwarded |
| Activity log can't be written | Request denied (audit is part of the decision) |
| Gate receives SIGTERM/SIGINT | Pending decisions denied, child server stopped (terminate → kill), socket cleaned up |
| Malformed or ambiguous JSON | Rejected before approval; never forwarded |

## 8. Configuration & state on disk

| Path (macOS / Linux) | Contents |
| --- | --- |
| `~/Library/Application Support/MCPApprovalGate/` / `~/.local/state/mcp-approval-gate/` | state folder, `0700` |
| `telegram-bot-token`, `telegram-pairing.json` | credentials, `0600` |
| `telegram-session.json` | 24 h session expiry, `0600` |
| `approvals.sock`, `daemon.lock`, `daemon.log` | daemon runtime |
| `activity.sqlite3` | decision history (no arguments) |
| `claude_desktop_config.json.bak-<timestamp>` | backups next to Claude's config before every change |

Overrides: `MCP_GATE_STATE_DIR`, `MCP_GATE_SOCKET` (see [CONFIGURATION.md](CONFIGURATION.md)).
