"""Keep the vault key in the operating system's keychain ("Unlock with this Mac").

macOS: the login Keychain via the built-in ``security`` tool. Commands are sent on stdin
(``security -i``), so the secret never appears in a process list. Reading it back may make
macOS ask for your login password, which is exactly the "use my Mac password" experience.
Linux: the Secret Service via ``secret-tool`` (GNOME Keyring / KWallet), when installed.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import sys

SERVICE = "Walled Vault"
ACCOUNT = "walled-vault-key"


def available() -> bool:
    if sys.platform == "darwin":
        return shutil.which("security") is not None
    if sys.platform.startswith("linux"):
        return shutil.which("secret-tool") is not None
    return False


def label() -> str:
    return "this Mac's Keychain" if sys.platform == "darwin" else "your system keyring"


def store(secret: str) -> None:
    if sys.platform == "darwin":
        command = (f"add-generic-password -U -a {shlex.quote(ACCOUNT)} -s {shlex.quote(SERVICE)} "
                   f"-l {shlex.quote(SERVICE)} -w {shlex.quote(secret)}\n")
        proc = subprocess.run(["security", "-i"], input=command, capture_output=True, text=True, timeout=30)
        if proc.returncode != 0 or "error" in proc.stderr.lower():
            raise OSError("Could not save to the Keychain")
        return
    proc = subprocess.run(["secret-tool", "store", "--label", SERVICE, "service", SERVICE, "account", ACCOUNT],
                          input=secret, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise OSError("Could not save to the system keyring")


def load() -> str | None:
    if sys.platform == "darwin":
        proc = subprocess.run(["security", "find-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w"],
                              capture_output=True, text=True, timeout=120)
    else:
        proc = subprocess.run(["secret-tool", "lookup", "service", SERVICE, "account", ACCOUNT],
                              capture_output=True, text=True, timeout=120)
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


def delete() -> None:
    if sys.platform == "darwin":
        subprocess.run(["security", "delete-generic-password", "-a", ACCOUNT, "-s", SERVICE],
                       capture_output=True, timeout=30)
    elif shutil.which("secret-tool"):
        subprocess.run(["secret-tool", "clear", "service", SERVICE, "account", ACCOUNT], capture_output=True, timeout=30)
