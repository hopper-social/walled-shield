# Approach: how this was designed and why

This is the reasoning behind MCP Approval Gate, written the way I'd explain it to another engineer: what I understood the problem to be, what I tried, what broke, and what I traded away.

## 1. Understanding the problem

Claude can act in the world through **MCP servers**: a filesystem server writes files, a GitHub server opens pull requests, a database server runs queries. Once a server is connected, Claude can call any of its tools, and nothing checks first.

The goal: **every individual tool call pauses until a human has seen what it will do and said yes.** Four requirements fell out of that:

1. **Interception must be real.** A blocked request must never reach the server, not "reach it and get rolled back".
2. **The decision must be informed.** "Allow `write_file`?" is useless without the path and the content.
3. **It must be reliable under real conditions:** several requests at once, Claude cancelling, the network dropping, the computer rebooting.
4. **Anything uncertain must resolve to *no*.** A gate that fails open is worse than no gate, because it gives false confidence.

The key observation that shaped everything: **MCP is just JSON-RPC.** Claude Desktop starts each local server as a child process and exchanges newline-delimited JSON messages over stdin/stdout. Remote servers speak the same messages over HTTP. If I can stand in the middle of that exchange, I can read every message and decide what passes.

## 2. Approaches considered

| Approach | Verdict | Why |
| --- | --- | --- |
| **Sniff network traffic** (pcap, TLS interception) | ❌ Rejected | Local servers use pipes, not sockets, so there is nothing to sniff. Claude Web traffic to connectors originates in Anthropic's cloud, not on the user's machine. Breaking TLS is invasive, fragile and needs root. |
| **Patch or inject into Claude Desktop** (Electron hooks, modified app bundle) | ❌ Rejected | Breaks on every update, violates app integrity, impossible to ship to other people. |
| **OS-level process hooking** (ptrace / DTrace on the server process) | ❌ Rejected | Needs elevated privileges, is platform-specific, and sees syscalls rather than MCP semantics, so it can't show a meaningful prompt. |
| **A proxy the user installs in the config** | ✅ Chosen | Uses the extension point Claude Desktop already has (the server launch command). Sees full MCP semantics, so it can block precisely and show useful context. Works with every MCP server unchanged. Fully reversible. |

So the gate is a **transparent stdio proxy** for Desktop and a **reverse HTTP proxy** for Claude Web custom connectors. Claude starts the gate thinking it is the server; the gate starts the real server as its own child and relays.

**What to hold.** The handshake (`initialize`), `ping` and the listing calls (`tools/list`, `resources/list`, …) change nothing, and blocking them would just make Claude think the server is broken. They pass automatically. Everything that *does* something (`tools/call`, `resources/read`, `prompts/get`, and anything unknown) is held. `--strict` holds even the listing calls for the paranoid.

## 3. The approval channel

The first version used a **local popup**: a Tk window that stays on top of everything else. It works, but only when you're at the computer. The real-life need was approving from a phone, so I added **Telegram**: free, available everywhere, with inline buttons and a decent bot API.

Each request becomes one message with Allow / Block buttons. Each button carries a random 128-bit nonce, and a tap is accepted only if it comes from the paired user, in the paired private chat, on the exact message that was sent for that request.

## 4. Challenges and how they were solved

**One bot, many servers.** Telegram allows exactly one long-polling consumer per bot; a second one makes the first fail. Claude Desktop starts *every* configured server at the same instant. The first design (each proxy polls the bot itself) worked perfectly with one server and silently broke with two. The fix was architectural: a single **approval daemon** owns the bot, and every proxy asks it over a private Unix socket. An `flock` guarantees one daemon even when ten proxies try to autostart it at the same moment.

**Concurrency.** Claude often fires several tool calls in parallel. Each one gets its own message and nonce, decisions are independent, and the proxy decides on a bounded worker pool so one slow human doesn't block an unrelated `ping`. Bounds everywhere (32 waiting decisions, 256 outstanding IDs) mean overload produces denials, never unbounded queues.

**Cancellation and late taps.** If Claude gives up on a request while it's waiting, approving it later would be dangerous: the action would run with nobody expecting it. Cancellation propagates: the proxy closes its daemon socket, the daemon withdraws the prompt and marks the card *Cancelled*, and a tap that arrives afterwards finds nothing pending.

**Informed decisions vs. leaking secrets.** Showing arguments is essential, but arguments contain API keys and passwords. Before anything is sent, values under credential-like keys and strings that look like known token formats (Anthropic/OpenAI keys, GitHub/Slack tokens, AWS keys, JWTs, bearer headers, private keys) are replaced with `«redacted»`. Oversized arguments are shortened with an explicit warning; `--deny-truncated` blocks anything that can't be shown in full, and `--hide-inputs` sends metadata only.

**Guarding everything, not just one server.** Asking users to hand-edit JSON for each server is how servers get missed. `wrap-desktop` rewrites the whole config in one step: backup first, atomic write, idempotent, preserves `env` and unknown keys, and `unwrap-desktop` restores the exact original. `status` flags anything left **UNGUARDED**.

**Staying on after reboots, crashes and expiry.** The daemon runs as a launchd/systemd user service with automatic restart. The 24-hour approval session can be renewed with `mcp-approval-gate renew` *without* stopping the daemon: the CLI shows a one-time code and the daemon waits for it from the paired chat.

**Packaging something people can actually run.** Claude Desktop launches servers with a minimal environment from an unknown working directory, and macOS privacy controls can stop Claude's child processes from reading code kept in Desktop, Documents or Downloads (`Operation not permitted`). So the product installs into its own virtual environment under `~/.local/share`, and wrapped entries call `<venv python> -m mcp_approval_gate`, which works regardless of PATH or working directory.

**A bug worth mentioning.** While refactoring the session check to return an expiry timestamp instead of a boolean, one branch still returned `False`, and the caller tested `is not None`, so a world-readable session file would have counted as valid. The existing permission test caught it before it shipped. That's the reason the test suite is treated as part of the security design rather than an afterthought.

## 5. Authentication, layer by layer

| Layer | Mechanism |
| --- | --- |
| Bot ownership | Bot token stored only in a `0600` file in a `0700` folder; injected into the daemon's environment by the launcher, never written to Claude's config or a command line. |
| Who may decide | One-time pairing: a random code is shown **on the computer** and must be sent from a **private chat**. That binds one Telegram user ID and chat ID, which is proof of being at the machine. Groups, channels, other users and bots are ignored. |
| Each decision | Callback must match user, chat, message ID and the request's 128-bit nonce. |
| Time-boxing | Approvals require an unexpired 24 h session file (owner-only, non-symlink, bound to the pairing). Renewal needs a fresh local code. `/pairout` revokes instantly. |
| Local IPC | Daemon socket is `0600` inside the `0700` state folder; only the same OS user can request decisions. |
| HTTP mode | Inbound `Authorization: Bearer` checked in constant time; loopback-only bind; client credentials never forwarded upstream; separate upstream token. |

## 6. Trade-offs I accepted

- **Coverage is bounded by architecture.** Only traffic that passes through the user's machine can be intercepted. Anthropic-hosted connectors and Claude's built-in tools are out of reach for any local tool. I documented that rather than over-claim.
- **Privacy vs. usefulness.** Telegram isn't end-to-end encrypted. Showing arguments makes approvals meaningful but exposes data to Telegram; redaction narrows that, and `--hide-inputs` or the local popup remove it.
- **Safety vs. friction.** Per-call approval plus a 24 h session is safe but adds taps. *Always allow* (one tool on one server, memory-only, cleared by `/clear`, `/pairout` or a restart) is the pressure valve, and it is deliberately narrow.
- **Human latency.** A tool call can wait up to the timeout (120 s). Long enough to reach for a phone, short enough that Claude isn't left hanging indefinitely.
- **Platform scope.** The daemon uses Unix sockets and launchd/systemd, so it targets macOS and Linux. Windows keeps the popup and the single-server Telegram mode.
- **Standard library only.** No dependency risk and trivial installs, at the cost of hand-writing a small Bot API client and HTTP proxy, which are covered by tests.

## 7. Cost management

- **Money: effectively zero.** No cloud service, no database server, no AI calls. The Telegram Bot API is free. Everything runs on the user's machine.
- **Machine resources: bounded by design.** One poller instead of one per server; capped pending prompts, request sizes (1 MiB), outstanding IDs and HTTP connections; long-polling instead of busy polling; the activity log keeps only the last 1,000 events.
- **Human attention: the real cost.** Handshake and listing calls never prompt, *Always allow* covers repetitive tools, and timeouts make an unanswered prompt resolve itself (to *no*).
- **Operational cost.** Self-healing service, a `status` command that answers "is it working?", a `test` command that proves the full round trip, and one-command uninstall.

## 8. How correctness is verified

The suite (129 tests) is organised around the invariant: *an action never reaches the server without an explicit allow for that exact request.* It checks allowed forwarding, blocked requests never reaching a real subprocess server, concurrency with mixed decisions, cancellation and late taps, timeouts, daemon/Telegram outages, session expiry and renewal, impersonation attempts, secret redaction, config wrap/unwrap round-trips, and the packaged CLI. The whole package passes `mypy --strict`. See [TESTING.md](TESTING.md).
