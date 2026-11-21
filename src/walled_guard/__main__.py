"""Command line for the Walled Guard desktop agent.

    walled-guard install       register with Chrome / Edge / Brave / Chromium / Arc
    walled-guard status        check the agent, OCR engine and browser registration
    walled-guard doctor        launch the agent the way Chrome does and pinpoint any failure
    walled-guard scan FILE...  inspect files exactly as an upload would be inspected
    walled-guard dialog-test   show the native confirmation dialog once
    walled-guard log           recent decisions (no content is ever stored)
    walled-guard uninstall     remove the browser registration
    walled-guard host          (started by the browser, not by you)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__, audit, dialogs, doctor, host, inspector, installer, ocr


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["host"]:
        # Started by the browser, which appends the caller's origin (and --parent-window=N on
        # Windows). Ignore them: rejecting them would make the host exit before the first
        # message, and the extension would only report "agent not found".
        return host.serve()
    parser = argparse.ArgumentParser(prog="walled-guard", description="Sensitive prompt and upload guard for ChatGPT and Perplexity.")
    parser.add_argument("--version", action="version", version=f"walled-guard {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    inst = sub.add_parser("install", help="register the agent with your browsers")
    inst.add_argument("--browser", action="append", help="only this browser (repeatable), e.g. chrome, edge, brave")
    inst.add_argument("--extension-id", default=installer.EXTENSION_ID, help=argparse.SUPPRESS)
    sub.add_parser("uninstall", help="remove the browser registration")
    sub.add_parser("status", help="check that everything is ready")
    scan = sub.add_parser("scan", help="inspect files like an upload")
    scan.add_argument("files", nargs="+", type=Path)
    sub.add_parser("dialog-test", help="show the confirmation dialog once")
    sub.add_parser("log", help="recent decisions")
    sub.add_parser("host", help="native-messaging host (started by the browser)")
    sub.add_parser("doctor", help="start the agent exactly the way the browser does and report what fails")
    args = parser.parse_args(argv)

    if args.cmd == "install":
        done = installer.install(args.extension_id, args.browser)
        if not done:
            print("No supported browser profile found. Use --browser chrome (or edge, brave, chromium) to force.")
            return 1
        print("Registered the desktop agent with:")
        for line in done:
            print(f"  ✓ {line}")
        print("\nNext: load the extension (chrome://extensions → Developer mode → Load unpacked → the 'extension' folder),")
        print("then restart the browser once.")
        if not ocr.available():
            print("\n⚠ Tesseract is not installed, so images and scanned PDFs cannot be checked.")
            print("  macOS: brew install tesseract   ·   Ubuntu: sudo apt install tesseract-ocr   ·   Windows: winget install UB-Mannheim.TesseractOCR")
        return 0

    if args.cmd == "uninstall":
        removed = installer.uninstall()
        print("Removed:\n" + "\n".join(f"  {r}" for r in removed) if removed else "Nothing to remove.")
        return 0

    if args.cmd == "doctor":
        return doctor.run()

    if args.cmd == "status":
        browsers = installer.installed()
        print(f"Walled Guard agent {__version__}")
        print(f"  Browsers registered : {', '.join(browsers) if browsers else 'none — run: walled-guard install'}")
        print(f"  Extension ID        : {installer.EXTENSION_ID}")
        print(f"  OCR (images, scans) : {'Tesseract found' if ocr.available() else 'NOT INSTALLED — images and scanned PDFs are not checked'}")
        print(f"  Dialog              : {dialogs.backend_name()}")
        print(f"  Decision log        : {audit.log_path()}")
        ok = bool(browsers) and not dialogs.backend_name().startswith("NONE")
        return 0 if ok else 1

    if args.cmd == "scan":
        flagged = 0
        for path in args.files:
            try:
                data = path.read_bytes()
            except OSError as exc:
                print(f"  ?  {path}: {exc.strerror}")
                continue
            r = inspector.inspect_file(path.name, data)
            flagged += r.found
            mark = "⚠ " if r.found else "✓ "
            detail = f"marker found ({', '.join(r.where)})" if r.found else "no marker"
            print(f"  {mark}{path.name:32} {r.kind:6} {detail}{'  [' + r.error + ']' if r.error else ''}")
        return 2 if flagged else 0

    if args.cmd == "dialog-test":
        choice = dialogs.ask(dialogs.Request("file", "ChatGPT", ["quarterly_report.pdf — page 2"]))
        print(f"You chose: {choice}")
        return 0

    if args.cmd == "log":
        entries = audit.recent()
        if not entries:
            print("No decisions recorded yet.")
        for e in entries:
            print(f"  {e.get('time')}  {e.get('site'):10}  {e.get('kind'):6}  {e.get('decision')}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
