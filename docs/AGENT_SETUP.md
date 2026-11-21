# Agent prompt: install, run and test Walled Shield

Copy everything below the line into a coding agent that runs **on your own Mac or Linux computer** (Claude Code, Cursor agent, etc.). It installs Walled, runs every automated test, walks you through the 3 steps only a human can do, and ends with a pass/fail report.

---

You are setting up **Walled Shield** (v2.6.0) on this computer, testing it end to end, and reporting the results. Walled guards what the user shares with AI. It has four parts:

- a desktop app;
- a background service;
- a Chrome extension that checks ChatGPT, Perplexity and Claude prompts and uploads;
- a Telegram bot for approvals.

It also includes **Pip**, a small pet guardian that lives in a screen corner.

## Rules
- Work in a terminal. Show each command before you run it, and stop on the first real failure: diagnose it, fix it, then continue.
- Never print, log or commit a Telegram bot token, a vault passphrase, or the contents of `~/.walled*` / `control.json`.
- Do not use `sudo` unless a step below says so, and ask me first.
- Some steps need me: loading the Chrome extension, pairing Telegram, and answering a native dialog. At each of these, tell me exactly what to click, then wait for me to say "done".
- Don't change project code to make a test pass. If something fails, report it with the error.

## 0. Find the project
The project is one folder, `walled-shield/`, which contains `pyproject.toml`, `install.sh`, `src/`, `tests/` and `docs/`. It is usually unzipped from `walled-shield-2.6.0.zip` or cloned from GitHub. Ask me where it is if you can't find it in `~/Downloads`, `~/Desktop` or the current directory. Set `PROJECT=<that path>`.

## 1. Check prerequisites
Run and report each:
```bash
uname -a
python3 --version                 # needs 3.10+
which google-chrome chromium brave-browser 2>/dev/null; ls /Applications | grep -Ei "chrome|brave|edge|arc|chromium"
command -v tesseract || echo "no tesseract (optional: reads text in screenshots)"
command -v brew || true
```
- Python missing or too old on macOS: run `xcode-select --install` or `brew install python@3.12`. On Linux: `sudo apt install python3 python3-venv` (ask me first).
- Optional on macOS with Homebrew: `brew install tesseract`.
- Optional on Linux: `sudo apt install tesseract-ocr zenity` (ask me first). zenity provides the native dialog.

## 2. Install (the same way a user does)
```bash
cd "$PROJECT"
bash install.sh                   # asks nothing; about a minute
```
The installer:
- installs into `~/.walled/venv`;
- registers the browser agent;
- starts the background service at login;
- adds the Walled app;
- opens Chrome on `chrome://extensions` with the extension folder path copied;
- opens the app.

If it fails, show the last 30 lines of `~/.walled/install.log`, fix the cause, and re-run.

Then put the CLI on the PATH for this session:
```bash
export PATH="$HOME/.walled/venv/bin:$PATH"
walled --version                  # expect 2.6.0
walled status
walled doctor                     # every line should be OK; fix anything it flags
```

## 3. Human step A: load the extension (about 20 seconds)
Run `walled extension`, then tell me:
1. Switch on **Developer mode** (top right of chrome://extensions).
2. Click **Load unpacked**, press ⌘⇧G (Linux: paste the path), ⌘V, Enter, then **Select**.
3. Open https://chatgpt.com. A small "Walled is watching over ChatGPT" card appears top right.

Wait for "done". Then verify the extension contacted the agent (`extension_seen` should be a timestamp):
```bash
"$HOME/.walled/venv/bin/python" - <<'EOF'
import json, urllib.request
from walled.control import control_file          # macOS: ~/Library/Application Support/WalledGuard/control.json
c = json.loads(control_file().read_text())      # Linux: ~/.local/state/walled-guard/control.json
r = urllib.request.Request(f"http://127.0.0.1:{c['port']}/api/state", headers={"X-Walled-Token": c["token"]})
s = json.loads(urllib.request.urlopen(r).read())
print("version", s["version"], "| extension seen:", s["guard"]["extension_seen"], "| browsers:", s["guard"]["browsers"])
EOF
```
This never prints the token. If the file is missing, the service isn't running: run `walled status` and `walled app`.

## 4. Human step B: pair Telegram (about 1 minute)
Tell me: open the Walled app (`walled app`) → **Telegram** → **Guided in Telegram Web**. Walled opens BotFather, I create a bot, and the token is filled in automatically. Then I press **START** on the deep link.

Wait for "done". Then:
```bash
walled status                     # Telegram should show as on
```

## 5. Live checks (I watch, you guide)
Do these one at a time and record pass/fail for each:

| # | Test | How | Expected |
|---|---|---|---|
| 1 | Native dialog | `walled guard dialog-test` | A native macOS/Linux dialog with Continue / Cancel appears |
| 2 | Prompt, Cancel | I paste `My notes WALLED_SENSITIVE` into ChatGPT, press Enter, choose **Cancel** | Not sent; the text stays in the box; Pip shows the green "I held that back" note |
| 3 | Prompt, Continue | Same, choose **Continue** | Sent unchanged |
| 4 | Clean prompt | `hello` | Sent with no dialog |
| 5 | File upload | Run `walled guard scan` on a sample file (below), then I upload it to ChatGPT | Dialog names the file (and the page for a PDF) |
| 6 | Perplexity + Claude.ai | Repeat test 2 on perplexity.ai and claude.ai | Same behaviour |
| 7 | Telegram | App → Test center → **Telegram Allow / Block** | An Allow/Block card arrives on my phone |
| 8 | Race | Test center → **First answer wins** | Same question on computer and phone; answering one closes the other |
| 9 | Pip | `walled pet` | Pip in the bottom-right corner: says hello once, stroking makes hearts and a purr, clicking opens his panel |
| 10 | Claude Desktop (if installed) | App → Claude tools → guard a server, then ask Claude to use it | Telegram asks Allow / Block; Pip shows "Claude is waiting for you" |

Sample files are in the app's Test center ("Sample files"), or in the source checkout after `python3 scripts/make_samples.py` (in `samples/`).

## 6. Automated tests
```bash
cd "$PROJECT"
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip
pip install -e ".[dev,ocr,window]"
python -m playwright install chromium
python scripts/make_samples.py

# unit + integration (no browser)
pytest -q tests/mcp tests/guard tests/platform
mypy src

# real-browser end-to-end (loads the extension into Chromium)
export CHROME="$(python -c 'from playwright.sync_api import sync_playwright as s; p=s().start(); print(p.chromium.executable_path); p.stop()')"
pytest -q tests/e2e                     # Linux without a display: xvfb-run -a pytest -q tests/e2e
deactivate
```
Expect about 327 tests passing and mypy clean. The e2e tests open browser windows; tell me not to touch them while they run.

## 7. Report
Finish with a short report:
- Environment: OS, Python, browser.
- Install: OK or failed, with fixes applied.
- Each human step: done or skipped.
- The 10 live checks: pass/fail, with one line on any failure.
- Automated tests: counts passed/failed; mypy result.
- Anything flaky or worth fixing, with the exact error text.

## Useful commands
```bash
walled app            # open Walled
walled pet            # put Pip back in the corner
walled extension      # reopen chrome://extensions with the path copied
walled doctor         # diagnose
walled status         # what's running
walled guard log      # recent decisions
walled mcp wrap-desktop    # guard Claude Desktop tools from the terminal
walled uninstall           # remove (keeps rules and vault); add --purge --yes to delete data
bash install.sh --uninstall   # also removes ~/.walled/venv
```
