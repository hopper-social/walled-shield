"""walled — one command for the whole product.

    walled app                 open the Control Center (starts the service if needed)
    walled service             run the background service in the foreground
    walled install             browser registration + start at login + Walled.app (macOS)
    walled uninstall [--purge] remove (and optionally delete your data)
    walled status              what is running and protected
    walled doctor              check every part and say what to fix
    walled extension           open chrome://extensions with the extension folder path copied
    walled pet                 put Pip, the little guardian, in the corner of your screen
    walled guard ...           browser-guard tools  (scan, doctor, dialog-test, log)
    walled mcp ...             MCP gate tools       (setup, wrap-desktop, renew, ...)
"""

from __future__ import annotations

import logging
import sys


def _service_logging() -> None:
    """stderr plus a rotating file (1 MB × 3) in the private state folder."""
    from logging.handlers import RotatingFileHandler

    from walled_guard import audit
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)
    try:
        handler = RotatingFileHandler(audit.private_dir() / "service.log", maxBytes=1_000_000, backupCount=3)
        handler.setFormatter(fmt)
        root.addHandler(handler)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd, rest = (argv[0], argv[1:]) if argv else ("help", [])
    if cmd == "host":                                     # started by the browser
        from walled_guard import host
        return host.serve()
    if cmd == "guard":
        from walled_guard.__main__ import main as guard_main
        return guard_main(rest)
    if cmd == "mcp":
        from mcp_approval_gate.main import main as mcp_main
        return mcp_main(rest)
    if cmd == "service":
        _service_logging()
        from .service import WalledService
        return WalledService().run(with_ui="--no-ui" not in rest)
    if cmd == "app":
        from .app import main as app_main
        return app_main(rest[0] if rest else "")
    if cmd == "pet":
        from . import pet
        return pet.run()
    if cmd == "extension":
        from .installation import extension_hint
        print(extension_hint())
        return 0
    if cmd in ("install", "uninstall", "status", "doctor"):
        from . import installation
        return int(getattr(installation, cmd)(rest))
    if cmd in ("--version", "version"):
        from . import __version__
        print(f"walled {__version__}")
        return 0
    print(__doc__)
    return 0 if cmd in ("help", "-h", "--help") else 2


if __name__ == "__main__":
    sys.exit(main())
