# Configuration reference

Run `mcp-approval-gate <command> --help` for the authoritative list.

## Commands

| Command | Purpose |
| --- | --- |
| `quickstart [--yes] [--config PATH]` | Guided first run: Telegram setup → service → guard Claude Desktop → test approval |
| `setup` | Telegram only: validate bot token, pair your private chat, start a 24 h session |
| `test` | Send a sample approval to your phone and report the result |
| `status [--config PATH]` | Daemon health, session, guarded/unguarded Desktop servers |
| `renew` | Re-enable approvals for 24 h via the running daemon (`/pair <code>` in the paired chat) |
| `wrap-desktop [--only a,b] [--exclude c] [--approver daemon\|telegram\|popup] [--strict] [--dry-run] [--config PATH]` | Route Claude Desktop servers through the gate (backup kept) |
| `unwrap-desktop [--only a,b] [--dry-run] [--config PATH]` | Restore original entries |
| `install-service` / `uninstall-service` | Login service (launchd / systemd `--user`) for the daemon |
| `uninstall [--purge] [--config PATH]` | Unwrap Desktop, remove the service; `--purge` also deletes credentials and history |
| `daemon [--timeout S] [--hide-inputs] [--deny-truncated]` | Run the approval daemon in the foreground (needs credentials in the environment; the service uses the launcher for that) |
| `stdio --server NAME [--approver popup\|telegram\|daemon] [--autostart] [--strict] -- CMD ARGS…` | Guard one local server (what `wrap-desktop` writes) |
| `http --upstream URL [--host 127.0.0.1] [--port 8765] [--approver …] [--autostart] [--strict]` | Guard a remote Streamable HTTP server ([guide](CLAUDE_WEB.md)) |
| `activity [--limit N]` | Recent decisions (no arguments recorded) |
| `permissions` | What this gate cannot approve (Claude / macOS permissions) |
| `pair-telegram`, `pair-session`, `pair-alert`, `pairout` | Low-level pairing commands for the single-server `--approver telegram` mode |

## Approvers

| `--approver` | Where you decide | Shows arguments | Notes |
| --- | --- | --- | --- |
| `daemon` (default for `wrap-desktop`) | Telegram, via the shared daemon | Yes, redacted (or hidden with `--hide-inputs`) | Many servers, one bot; *Always allow*; renew without restart |
| `popup` | Topmost window on this computer | Yes, in full (oversized requests blocked) | Needs a graphical session and Tk; 60 s timeout |
| `telegram` | Telegram, polled by this one proxy | No (metadata only) | Original mode; only one guarded server per bot |

## Environment variables

| Variable | Used by | Meaning |
| --- | --- | --- |
| `MCP_GATE_STATE_DIR` | all | Override the private state folder |
| `MCP_GATE_SOCKET` | daemon, proxies | Override the daemon socket path |
| `MCP_GATE_TIMEOUT` | daemon | Seconds before an unanswered prompt is blocked (default 120) |
| `MCP_GATE_HIDE_INPUTS=1` | daemon | Never send argument values to Telegram |
| `MCP_GATE_DENY_TRUNCATED=1` | daemon | Auto-block requests too large to preview in full |
| `MCP_TELEGRAM_BOT_TOKEN`, `MCP_TELEGRAM_CHAT_ID`, `MCP_TELEGRAM_USER_ID` | daemon, telegram mode | Credentials (normally injected by the launcher from private files) |
| `MCP_TELEGRAM_ALERT_CHAT_ID` | daemon, telegram mode | Optional separate group/channel for informational alerts |
| `MCP_TELEGRAM_SESSION_FILE` | daemon, telegram mode | Session file location (set by the launcher) |
| `MCP_APPROVAL_TOKEN` | http | Required inbound bearer token |
| `MCP_UPSTREAM_TOKEN` | http | Optional bearer token sent to the upstream |
| `XDG_STATE_HOME`, `XDG_CONFIG_HOME` | Linux | Standard base directories for state and Claude's config |

Installer overrides (`install.sh`): `MCP_GATE_HOME`, `MCP_GATE_SOURCE`, `MCP_GATE_PYTHON`, `MCP_GATE_NO_QUICKSTART=1`.

## Files

| File | Location |
| --- | --- |
| Claude Desktop config | macOS `~/Library/Application Support/Claude/claude_desktop_config.json`, Linux `~/.config/Claude/claude_desktop_config.json`, Windows `%APPDATA%\Claude\claude_desktop_config.json` |
| State folder | macOS `~/Library/Application Support/MCPApprovalGate/`, Linux `~/.local/state/mcp-approval-gate/` |
| launchd agent | `~/Library/LaunchAgents/com.mcpapprovalgate.daemon.plist` |
| systemd unit | `~/.config/systemd/user/mcp-approval-gate.service` |
| Program (installer) | `~/.local/share/mcp-approval-gate/venv`, command linked at `~/.local/bin/mcp-approval-gate` |
