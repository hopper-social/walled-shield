"""Native desktop confirmation dialog: Continue or Cancel.

The dialog is a real operating-system window shown by this desktop agent, never an
HTML popup inside the browser page:

* macOS   — AppleScript ``display dialog`` (via osascript)
* Linux   — zenity, then kdialog, then a Tk window
* Windows — a topmost Tk window (custom Continue / Cancel buttons), falling back to a
            system MessageBox where Tk is unavailable

"Cancel" is the default button, closing the dialog counts as Cancel, and no answer
within ``TIMEOUT_S`` seconds counts as Cancel. Anything that goes wrong while showing
the dialog also counts as Cancel: the guard fails closed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import sys
from dataclasses import dataclass, field
from pathlib import Path

TIMEOUT_S = 120
TITLE = "Walled Guard — sensitive content"
CONTINUE, CANCEL = "continue", "cancel"


@dataclass
class Request:
    kind: str            # "prompt" | "file" | "fill" (password fill confirmation)
    site: str            # "ChatGPT" | "Perplexity"
    items: list[str]     # prompt preview, or one line per file ("report.pdf — page 2")
    rules: list[str] = field(default_factory=list)   # names of the rules that matched
    phone: bool = False  # the same question is also on Telegram

    def message(self) -> tuple[str, str]:
        """Headline and body text shared by every backend."""
        if self.kind == "fill":
            return (f"Fill your password for {self.site}?",
                    f"Account: {self.items[0] if self.items else '(no username)'}\n\n"
                    "Continue fills it once in your browser. Cancel leaves the form empty.")
        only_marker = not self.rules or self.rules == ["WALLED_SENSITIVE marker"]
        what = "Sensitive marker" if only_marker else "Sensitive data"
        matched = "" if only_marker else "Matched: " + ", ".join(self.rules[:6]) + "\n\n"
        phone = "\n\nYou can also answer on Telegram; the first answer counts." if self.phone else ""
        if self.kind == "prompt":
            headline = f"{what} found in a text prompt on {self.site}"
            lead = "Your prompt contains WALLED_SENSITIVE:" if only_marker else "Your prompt:"
            body = (f"{matched}{lead}\n\n"
                    f"“{self.items[0]}”\n\n"
                    f"Continue sends the prompt unchanged. Cancel keeps it in the box so you can edit it.{phone}")
        else:
            label = "a file" if len(self.items) == 1 else f"{len(self.items)} files"
            if all("not checked" in item for item in self.items):
                headline = f"{label.capitalize()} being uploaded to {self.site} could not be checked"
                lead = "Walled Guard could not read:"
            else:
                headline = f"{what} found in {label} being uploaded to {self.site}"
                lead = "WALLED_SENSITIVE was found in:" if only_marker else "Found in:"
            listing = "\n".join(f"• {item}" for item in self.items)
            body = (f"{matched}{lead}\n\n{listing}\n\n"
                    f"Continue uploads the file unchanged. Cancel stops the upload.{phone}")
        return headline, body


def preview(text: str, limit: int = 160) -> str:
    """One-line excerpt of a prompt, centred on the marker so the user sees it."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    at = flat.find("WALLED_SENSITIVE")
    start = max(0, min(at - limit // 3, len(flat) - limit)) if at >= 0 else 0
    excerpt = flat[start:start + limit]
    return ("…" if start > 0 else "") + excerpt + ("…" if start + limit < len(flat) else "")


def ask(request: Request, cancel: threading.Event | None = None) -> str:
    """Show the dialog and return CONTINUE or CANCEL. Never raises.

    ``cancel`` closes the dialog early (the question was answered on Telegram first).
    """
    cancel = cancel or threading.Event()
    forced = os.environ.get("WALLED_GUARD_AUTO_ANSWER")      # automated tests only
    if forced in (CONTINUE, CANCEL):
        return forced
    if forced == "wait":                                      # tests: nobody at the computer
        cancel.wait(TIMEOUT_S)
        return CANCEL
    try:
        if sys.platform == "darwin":
            return _macos(request, cancel)
        if sys.platform.startswith("win"):
            return _windows(request, cancel)
        return _linux(request, cancel)
    except Exception as exc:          # no dialog could be shown: block, and say why in Chrome's log
        print(f"walled-guard: dialog failed, treating as Cancel: {type(exc).__name__}: {exc}", file=sys.stderr)
        return CANCEL


def _run(command: list[str], cancel: threading.Event) -> tuple[int, str]:
    """Run a dialog program; kill it if ``cancel`` is set. Returns (exit code, stdout)."""
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    # A watcher kills the dialog the moment the question is answered elsewhere (or times out);
    # this thread just blocks on communicate(): no polling loop.
    finished = threading.Event()

    def watch() -> None:
        if not cancel.wait(TIMEOUT_S + 15) or not finished.is_set():
            if proc.poll() is None:
                proc.kill()
    if not cancel.is_set():
        threading.Thread(target=watch, daemon=True).start()
    else:
        proc.kill()
    out, _ = proc.communicate()
    finished.set()
    cancel_hit = cancel.is_set() or proc.returncode < 0
    return (-1, "") if cancel_hit else (proc.returncode, out or "")


# ---------------------------------------------------------------- macOS
def _applescript_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def macos_script(request: Request) -> str:
    headline, body = request.message()
    return (
        "tell me to activate\n"
        f"display dialog {_applescript_string(body)} with title {_applescript_string(headline)} "
        'buttons {"Cancel", "Continue"} default button "Cancel" cancel button "Cancel" '
        f"with icon caution giving up after {TIMEOUT_S}"
    )


ICON = Path(__file__).resolve().parents[1] / "walled" / "web" / "icon.png"


def parts(request: Request) -> tuple[str, str, str]:
    """(headline, explanation, detail box) for the native alert."""
    site = request.site
    if request.kind == "fill":
        return (f"Fill your password for {site}?", "Walled will fill this login once in your browser.",
                f"Account: {request.items[0] if request.items else '(no username)'}")
    matched = ", ".join(request.rules[:6]) if request.rules else "WALLED_SENSITIVE marker"
    phone = "\n\nYou can also answer on Telegram. The first answer counts." if request.phone else ""
    if request.kind == "prompt":
        return (f"Sensitive data in your {site} prompt",
                "Walled stopped this prompt before it was sent.\n\n"
                "Continue sends it unchanged. Cancel keeps it in the box so you can edit it." + phone,
                f"Found: {matched}\n\n“{request.items[0] if request.items else ''}”")
    unchecked = all("not checked" in item for item in request.items)
    noun = "file" if len(request.items) == 1 else f"{len(request.items)} files"
    return ((f"Walled couldn't check {'this' if noun == 'file' else 'these'} {noun} for {site}" if unchecked
             else f"Sensitive data in a {noun} for {site}" if noun == "file" else f"Sensitive data in {noun} for {site}"),
            "Walled stopped the upload before anything was sent.\n\n"
            "Continue uploads it unchanged. Cancel stops the upload." + phone,
            ("" if unchecked else f"Found: {matched}\n\n") + "\n".join(f"• {i}" for i in request.items))


def macos_alert_script(request: Request) -> str:
    """A real macOS alert (NSAlert via JavaScript for Automation): Walled icon, clear headline,
    the prompt or file list in a scrollable box, Cancel as the default button."""
    headline, info, detail = parts(request)
    return f"""
ObjC.import('Cocoa');
var app = $.NSApplication.sharedApplication;
app.setActivationPolicy(1);
var a = $.NSAlert.alloc.init;
a.messageText = {json.dumps(headline)};
a.informativeText = {json.dumps(info)};
a.alertStyle = 0;
var icon = $.NSImage.alloc.initWithContentsOfFile({json.dumps(str(ICON))});
if (icon && !icon.isNil()) {{ a.icon = icon; }}
var cancel = a.addButtonWithTitle('Cancel');
var go = a.addButtonWithTitle('Continue');
var box = $.NSScrollView.alloc.initWithFrame($.NSMakeRect(0, 0, 440, 110));
box.hasVerticalScroller = true; box.borderType = 2;
var text = $.NSTextView.alloc.initWithFrame($.NSMakeRect(0, 0, 440, 110));
text.editable = false; text.selectable = true; text.string = {json.dumps(detail)};
var font = $.NSFont.monospacedSystemFontOfSizeWeight ? $.NSFont.monospacedSystemFontOfSizeWeight(12, 0) : $.NSFont.userFixedPitchFontOfSize(12);
text.font = font;
box.documentView = text;
a.accessoryView = box;
a.window.title = 'Walled';
a.window.level = 3;
app.activateIgnoringOtherApps(true);
var r = a.runModal;
r == 1001 ? 'continue' : 'cancel';
"""


def _macos(request: Request, cancel: threading.Event) -> str:
    code, out = _run(["osascript", "-l", "JavaScript", "-e", macos_alert_script(request)], cancel)
    if code == 0 and out.strip() in ("continue", "cancel"):
        return CONTINUE if out.strip() == "continue" else CANCEL
    if code == -1 or cancel.is_set():                       # timed out or answered on the phone
        return CANCEL
    # Very old macOS or JXA unavailable: the classic AppleScript dialog.
    code, out = _run(["osascript", "-e", macos_script(request)], cancel)
    if code == 0 and "button returned:Continue" in out and "gave up:true" not in out:
        return CONTINUE
    return CANCEL


# ---------------------------------------------------------------- Linux
def linux_command(request: Request) -> list[str] | None:
    headline, body = request.message()
    if shutil.which("zenity"):
        return ["zenity", "--question", f"--title={TITLE}", f"--text=<b>{_pango(headline)}</b>\n\n{_pango(body)}",
                "--ok-label=Continue", "--cancel-label=Cancel", "--default-cancel",
                f"--window-icon={ICON}" if ICON.exists() else "--icon-name=dialog-warning", "--icon-name=dialog-warning",
                f"--timeout={TIMEOUT_S}", "--width=520"]
    if shutil.which("kdialog"):
        return ["kdialog", "--title", TITLE, "--warningyesno", f"{headline}\n\n{body}",
                "--yes-label", "Continue", "--no-label", "Cancel"]
    if shutil.which("xmessage"):        # bare X11; exit code 10 = Continue (a timeout exits 0)
        return ["xmessage", "-center", "-buttons", "Cancel:1,Continue:10", "-default", "Cancel",
                "-timeout", str(TIMEOUT_S), f"{headline}\n\n{body}"]
    return None


def _pango(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _linux(request: Request, cancel: threading.Event) -> str:
    command = linux_command(request)
    if command is None:
        return _tk(request, cancel)
    code, _ = _run(command, cancel)
    approved = 10 if command[0] == "xmessage" else 0          # zenity: 1 = Cancel, 5 = timeout
    return CONTINUE if code == approved else CANCEL


# ---------------------------------------------------------------- Windows
def _windows(request: Request, cancel: threading.Event) -> str:
    try:
        return _tk(request, cancel)
    except ImportError:
        import ctypes
        headline, body = request.message()
        MB_OKCANCEL, MB_ICONWARNING, MB_DEFBUTTON2, MB_TOPMOST, MB_SETFOREGROUND = 0x1, 0x30, 0x100, 0x40000, 0x10000
        text = f"{headline}\n\n{body}\n\nOK = Continue    Cancel = Cancel"
        answer = ctypes.windll.user32.MessageBoxW(None, text, TITLE,  # type: ignore[attr-defined]
                                                  MB_OKCANCEL | MB_ICONWARNING | MB_DEFBUTTON2 | MB_TOPMOST | MB_SETFOREGROUND)
        return CONTINUE if answer == 1 else CANCEL


# ---------------------------------------------------------------- Tk (Windows, Linux fallback)
def _tk(request: Request, cancel: threading.Event | None = None) -> str:
    import tkinter as tk

    headline, body = request.message()
    choice = {"value": CANCEL}
    root = tk.Tk()
    root.title(TITLE)
    root.attributes("-topmost", True)
    root.resizable(False, False)
    frame = tk.Frame(root, padx=22, pady=18)
    frame.pack()
    tk.Label(frame, text=headline, font=("Segoe UI", 12, "bold"), wraplength=480, justify="left").pack(anchor="w")
    tk.Label(frame, text=body, font=("Segoe UI", 10), wraplength=480, justify="left").pack(anchor="w", pady=(10, 16))
    buttons = tk.Frame(frame)
    buttons.pack(anchor="e")

    def decide(value: str) -> None:
        choice["value"] = value
        root.destroy()

    cancel_button = tk.Button(buttons, text="Cancel", width=12, command=lambda: decide(CANCEL))
    cancel_button.pack(side="right", padx=(8, 0))
    tk.Button(buttons, text="Continue", width=12, command=lambda: decide(CONTINUE)).pack(side="right")
    cancel_button.focus_set()
    root.bind("<Escape>", lambda _e: decide(CANCEL))
    root.protocol("WM_DELETE_WINDOW", lambda: decide(CANCEL))
    root.after(TIMEOUT_S * 1000, lambda: decide(CANCEL))

    def watch() -> None:
        if cancel is not None and cancel.is_set():
            decide(CANCEL)
        else:
            root.after(150, watch)
    root.after(150, watch)
    root.lift()
    root.focus_force()
    root.mainloop()
    return choice["value"]


def backend_name() -> str:
    if os.environ.get("WALLED_GUARD_AUTO_ANSWER"):
        return "auto-answer (test mode)"
    if sys.platform == "darwin":
        return "macOS dialog (osascript)"
    if sys.platform.startswith("win"):
        return "Windows dialog (Tk / MessageBox)"
    command = linux_command(Request("prompt", "", [""]))
    if command:
        return command[0]
    return "Tk window" if tk_available() else "NONE — install zenity (sudo apt install zenity)"


def tk_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("tkinter") is not None
