# Testing

```bash
python -m unittest discover -s tests -v    # 129 tests, ~25 s, no network or accounts needed
mypy                                        # strict type checking (configured in pyproject.toml)
```

Telegram is replaced by an in-memory fake Bot API, so taps, edits and failures can be driven deterministically. MCP servers in tests are **real subprocesses** (`mcp_approval_gate.examples.notes_server`), so "blocked requests never reach the server" is checked by looking at the filesystem, not by mocks.

## Automated test map

| File | What it proves |
| --- | --- |
| `test_proxy.py` | Gate allowlist and strict mode; denied requests never written to the child; ID preservation; duplicate/invalid IDs; batches refused; JSON ambiguity rejected; cancellation before forwarding; child crash fails outstanding requests; HTTP auth, header stripping, redirects, SSE, size limits; popup preview limits; audit contains no arguments |
| `test_real_use.py` | End-to-end through stdio and HTTP with the real notes server: approved write persists, blocked write never happens, parallel requests, failures mid-flight |
| `test_daemon.py` | Shared daemon: Allow / Block / timeout; card shows arguments; **secret redaction**; *Always allow* scoped per tool and cleared by `/clear`; **6 concurrent requests with mixed decisions**; **cancellation withdraws the prompt and a late tap is ignored**; status/unknown ops; expired session denies and notifies; renewal only from the paired user with the right code; `/pairout` clears rules; daemon missing → deny; real notes server behind the stdio proxy through the daemon; Desktop config wrap/unwrap round-trip, only/exclude/dry-run |
| `test_product.py` | Wrapped entries run `python -m mcp_approval_gate`; legacy entries upgraded, not double-wrapped; original `--` arguments survive; `test` command reports Allow/Block/missing daemon; `uninstall` restores config and purges only with `--purge`; launchd/systemd definitions; `--version`, `status` |
| `test_security.py` | Attack-by-attack: 22 secret formats redacted with ordinary values intact; cards always fit Telegram's 4096-character limit with hostile input (HTML, quotes, emoji, deep nesting); HTML injection blocked; forged taps (other user, group, other message, tampered code, replay) rejected; *Always allow* scoped per server; socket `0600` and cannot be hijacked; malformed, oversized and flooding clients denied; private-folder and symlink checks; gate credentials not inherited by MCP servers; **fuzzing: 3,000 crafted messages with every decision 'Block' and no action forwarded**; strict mode; ambiguous JSON rejected |
| `test_telegram.py` | Pairing codes, private-chat and user checks, callback validation, nonce/message binding, early taps, polling failure, session file security (permissions, symlinks, corruption, expiry), `/pairout`, alert channel pairing |
| `test_telegram_launcher.py` | Credentials loaded from private files only; token never on the command line; exec uses the installed module |
| `test_notes_server.py`, `test_html_files.py` | The example MCP servers: path confinement, symlink refusal, atomic writes, protocol responses |

CI runs the suite on macOS and Ubuntu with Python 3.10–3.13, runs `mypy --strict`, builds the wheel, installs it into a clean virtual environment, smoke-tests the CLI and runs `install.sh`.

## Manual acceptance checklist (real Claude Desktop + phone)

Run these before a release or a demo recording.

**Setup**
1. `./install.sh` on a clean user account → quickstart completes; `status` shows daemon running, approvals ON, servers 🔒 guarded.
2. Send `/pair <code>` from a **group** → nothing happens. From a second Telegram account → nothing happens.

**Core flow**
3. Ask Claude to write a note called `approved` → card shows tool, server, arguments → **Allow** → file exists, card shows ✅.
4. Same with `blocked` → **Block** → Claude reports the tool was blocked, no file, card shows ❌.
5. Ignore a card → after 120 s Claude gets an error, card shows ⌛.
6. Opening Claude and listing tools produces **no** card.

**Concurrency**
7. "Write notes a, b and c" → three separate cards; Allow one, Block one, ignore one → each outcome correct.
8. Guard two different servers and use both → approvals work for both (one bot).

**Edge cases**
9. Stop Claude's response while a card is waiting → card shows *Cancelled by Claude*; tapping Allow afterwards does nothing.
10. Turn off Wi-Fi while a card is waiting → request is blocked.
11. `mcp-approval-gate uninstall-service`, restart Claude Desktop → guarded servers start the daemon on demand (or fail closed if credentials are missing); nothing runs unapproved.
12. `/pairout` → every action blocked with a notice; `mcp-approval-gate renew` restores approvals without restarting anything.
13. *Always allow* on one tool → next call runs without a card, a different tool still asks; `/clear` → asks again.
14. A tool call with `api_key` / `sk-ant-…` in its arguments → card shows `«redacted»`.

**Reliability**
15. Reboot → `status` shows the daemon running.
16. `kill` the daemon process → service restarts it within ~10 s.
17. `mcp-approval-gate uninstall` → `claude_desktop_config.json` identical to the original.
