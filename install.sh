#!/usr/bin/env bash
# Walled Shield installer for macOS and Linux. No questions asked; takes about a minute.
#   Double-click "Install Walled.command" (macOS), or run:  bash install.sh
#   bash install.sh --uninstall
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="${WALLED_VENV:-$HOME/.walled/venv}"
LOG="$HOME/.walled/install.log"
mkdir -p "$HOME/.walled"
: > "$LOG"

bold=$'\033[1m'; dim=$'\033[2m'; org=$'\033[38;5;208m'; grn=$'\033[32m'; red=$'\033[31m'; off=$'\033[0m'
step() { printf '%s  %s%s' "$org●$off" "$*" "$dim…$off"; }
ok()   { printf '\r\033[K%s  %s\n' "$grn✓$off" "$*"; }
fail() {
  printf '\r\033[K%s  %s\n\n' "$red✗$off" "$*"
  echo "${dim}Last lines of $LOG:${off}"; tail -n 15 "$LOG" || true
  echo; echo "Need help? Send that log, or run: bash -x \"$HERE/install.sh\""
  exit 1
}
quiet() { "$@" >>"$LOG" 2>&1; }

cat <<'PIP'

      ▄▀▀▀▄
     █ ▮ ▮ █    Hi, I'm Pip from Walled.
      ▀▄▄▄▀     Setting things up — no questions, about a minute.

PIP

if [[ "${1:-}" == "--uninstall" ]]; then
  [[ -x "$VENV/bin/walled" ]] && "$VENV/bin/walled" uninstall --yes || true
  rm -rf "$VENV"
  ok "Walled removed. Remove the extension too: chrome://extensions → Walled → Remove."
  exit 0
fi

# 1 — Python. macOS ships it with the Command Line Tools; offer those if it's missing.
step "Checking Python"
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then PY="$(command -v "$c")"; break; fi
done
if [[ -z "$PY" ]]; then
  if [[ "$(uname)" == "Darwin" ]]; then
    xcode-select --install >/dev/null 2>&1 || true
    fail "Python 3.10+ is needed. A window to install Apple's Command Line Tools just opened (or run: brew install python). Then double-click the installer again."
  fi
  fail "Python 3.10+ is needed: sudo apt install python3 python3-venv   (or your distro's equivalent)"
fi
ok "Python $("$PY" -c 'import platform; print(platform.python_version())')"

# 2 — The app, in its own environment (nothing touches your system Python).
step "Installing Walled"
quiet "$PY" -m venv "$VENV" || fail "Couldn't create $VENV (on Debian/Ubuntu: sudo apt install python3-venv)"
quiet "$VENV/bin/python" -m pip install --upgrade pip || true
WHEEL="$(ls "$HERE"/walled_shield-*.whl 2>/dev/null | head -1 || true)"
quiet "$VENV/bin/python" -m pip install "${WHEEL:-$HERE}[ocr]" || fail "Installing the app failed (are you online?)"
"$VENV/bin/walled" --version >/dev/null 2>&1 || fail "The app didn't start after installing"
quiet "$VENV/bin/python" -m pip install "pywebview>=5" || true          # nicer window when available
ok "Walled $("$VENV/bin/walled" --version 2>/dev/null | awk '{print $NF}')"

# 3 — Optional extras, never blocking. Tesseract lets Walled read text inside screenshots.
if ! command -v tesseract >/dev/null 2>&1 && [[ "$(uname)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then
  step "Adding screenshot reading (Tesseract) in the background"
  (brew install tesseract >>"$HOME/.walled/tesseract.log" 2>&1 &) ; ok "Screenshot reading installing in the background"
fi

# 4 — Register with browsers, start at login, app icon, extension folder. Then open Chrome + the app.
step "Connecting to your browsers"
quiet "$VENV/bin/walled" install --no-open "$@" || fail "Setup failed"
ok "Browser agent, background service and app icon ready"

echo
"$VENV/bin/walled" extension || true
( "$VENV/bin/walled" app >>"$LOG" 2>&1 & ) || true

cat <<MSG

${bold}That's it.${off} Walled is open — Pip walks you through the rest (Telegram, rules, passwords).
Open it later from Launchpad / Spotlight / your app menu: ${bold}Walled${off}.
Something off? ${dim}$VENV/bin/walled doctor${off}
MSG
