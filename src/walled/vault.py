"""Local password vault.

* One file, encrypted as a whole with AES-256-GCM. The key is derived from the master
  password with scrypt (N=2^15, r=8, p=1) and a random salt; a wrong password simply fails
  authentication. Nothing about the entries (not even site names) is readable without it.
* The key lives only in the memory of the running Walled service and is wiped on lock,
  on auto-lock after inactivity, on `/lock` from Telegram, and when the service stops.
* Passwords are never sent to Telegram. Telegram can only approve a fill or lock the vault.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import string
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

AAD = b"walled-vault-v1"
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}


class VaultError(Exception):
    pass


class Locked(VaultError):
    pass


@dataclass
class Entry:
    origin: str                     # normalised host, e.g. "github.com"
    username: str
    password: str
    note: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("password")
        return d


def host_of(origin: str) -> str:
    origin = origin.strip().lower()
    if "://" not in origin:
        origin = "https://" + origin
    host = urlsplit(origin).hostname or ""
    return host[4:] if host.startswith("www.") else host


def matches(entry_host: str, page_host: str) -> bool:
    """Same host, or the page is a subdomain of the saved host (accounts.github.com → github.com)."""
    page_host = host_of(page_host)
    return bool(entry_host) and (page_host == entry_host or page_host.endswith("." + entry_host))


def generate(length: int = 20, symbols: bool = True) -> str:
    length = max(12, min(128, length))
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    extra = "!@#$%^&*-_=+?" if symbols else ""
    pool = alphabet + extra
    while True:
        pw = "".join(secrets.choice(pool) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw)
                and (not symbols or any(c in extra for c in pw))):
            return pw


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT["n"], r=SCRYPT["r"],
                          p=SCRYPT["p"], maxmem=128 * 1024 * 1024, dklen=32)


class Vault:
    def __init__(self, path: Path, autolock_minutes: float = 15):
        self.path = path
        self.autolock = autolock_minutes * 60
        self._key: bytes | None = None
        self._salt: bytes | None = None
        self._entries: list[Entry] = []
        self._last_use = 0.0
        self._failures = 0
        self._mode = "passphrase"           # "passphrase" (you type it) or "keychain" (kept by the OS)
        self._lock = threading.RLock()

    # ----- state ----------------------------------------------------------------------
    def exists(self) -> bool:
        return self.path.exists()

    def unlocked(self) -> bool:
        with self._lock:
            if self._key is not None and time.monotonic() - self._last_use > self.autolock:
                self.lock()
            return self._key is not None

    def mode(self) -> str:
        try:
            return str(json.loads(self.path.read_text(encoding="utf-8")).get("mode", "passphrase"))
        except (OSError, ValueError):
            return "passphrase"

    def status(self) -> dict[str, Any]:
        from . import keychain
        return {"exists": self.exists(), "unlocked": self.unlocked(),
                "entries": len(self._entries) if self.unlocked() else None,
                "mode": self.mode() if self.exists() else None,
                "keychain_available": keychain.available(), "keychain_label": keychain.label()}

    def lock(self) -> None:
        with self._lock:
            self._key = None
            self._entries = []

    def _touch(self) -> None:
        if not self.unlocked():
            raise Locked("vault is locked")
        self._last_use = time.monotonic()

    # ----- create / unlock -------------------------------------------------------------
    def create(self, master: str, mode: str = "passphrase") -> None:
        if len(master) < 10:
            raise VaultError("Use a vault passphrase of at least 10 characters.")
        with self._lock:
            if self.exists():
                raise VaultError("a vault already exists")
            self._mode = mode
            self._salt = os.urandom(16)
            self._key = _derive(master, self._salt)
            self._entries = []
            self._last_use = time.monotonic()
            self._save()

    def unlock(self, master: str) -> None:
        with self._lock:
            if self._failures >= 5:
                time.sleep(min(30, 2 ** (self._failures - 4)) * (0.8 + secrets.randbelow(400) / 1000))  # slow down guessing
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                salt = base64.b64decode(data["kdf"]["salt"])
                key = _derive(master, salt)
                plain = AESGCM(key).decrypt(base64.b64decode(data["nonce"]), base64.b64decode(data["data"]), AAD)
            except FileNotFoundError as exc:
                raise VaultError("no vault yet") from exc
            except Exception as exc:
                self._failures += 1
                raise VaultError("That isn't the vault passphrase. It's the one you chose when you created the vault, "
                                 "not your computer password.") from exc
            self._failures = 0
            self._mode = str(data.get("mode", "passphrase"))
            self._salt, self._key = salt, key
            self._entries = [Entry(**e) for e in json.loads(plain)["entries"]]
            self._last_use = time.monotonic()

    def change_master(self, old: str, new: str) -> None:
        if len(new) < 10:
            raise VaultError("Use a vault passphrase of at least 10 characters.")
        with self._lock:
            self.unlock(old)
            self._mode = "passphrase"
            self._salt = os.urandom(16)
            self._key = _derive(new, self._salt)
            self._save()

    def _save(self) -> None:
        assert self._key is not None and self._salt is not None
        nonce = os.urandom(12)
        payload = json.dumps({"entries": [asdict(e) for e in self._entries]}).encode()
        blob = {"version": 1, "mode": self._mode, "kdf": {"name": "scrypt", **SCRYPT, "salt": base64.b64encode(self._salt).decode()},
                "nonce": base64.b64encode(nonce).decode(),
                "data": base64.b64encode(AESGCM(self._key).encrypt(nonce, payload, AAD)).decode()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(blob, fh)
        os.replace(tmp, self.path)

    # ----- OS keychain ("Unlock with this Mac") -----------------------------------------
    def create_with_keychain(self) -> None:
        """A random 256-bit key kept in the OS keychain: nothing to remember or type."""
        from . import keychain
        if not keychain.available():
            raise VaultError("No system keychain is available here. Choose a vault passphrase instead.")
        secret = base64.urlsafe_b64encode(os.urandom(32)).decode()
        keychain.store(secret)
        self.create(secret, mode="keychain")

    def unlock_with_keychain(self) -> None:
        from . import keychain
        secret = keychain.load()
        if not secret:
            raise VaultError("The keychain didn't give Walled the key (cancelled, or the entry is missing).")
        self.unlock(secret)

    # ----- entries --------------------------------------------------------------------
    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            self._touch()
            return [e.public() for e in sorted(self._entries, key=lambda e: (e.origin, e.username))]

    def get(self, entry_id: str) -> Entry:
        with self._lock:
            self._touch()
            for e in self._entries:
                if e.id == entry_id:
                    return e
            raise VaultError("no such entry")

    def find(self, page: str) -> list[Entry]:
        with self._lock:
            self._touch()
            return [e for e in self._entries if matches(e.origin, page)]

    def save_entry(self, origin: str, username: str, password: str, note: str = "",
                   entry_id: str | None = None) -> Entry:
        host = host_of(origin)
        if not host or not password:
            raise VaultError("site and password are required")
        if len(password) > 1024 or len(username) > 512 or len(note) > 4000:
            raise VaultError("entry too large")
        with self._lock:
            self._touch()
            existing = next((e for e in self._entries if (entry_id and e.id == entry_id)
                             or (not entry_id and e.origin == host and e.username == username)), None)
            if existing:
                existing.origin, existing.username, existing.password = host, username, password
                existing.note, existing.updated = note or existing.note, time.time()
                entry = existing
            else:
                entry = Entry(host, username, password, note)
                self._entries.append(entry)
            self._save()
            return entry

    def delete(self, entry_id: str) -> None:
        with self._lock:
            self._touch()
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.id != entry_id]
            if len(self._entries) == before:
                raise VaultError("no such entry")
            self._save()


def strength(password: str) -> int:
    """0–4, rough: length and character variety (shown as a meter, not a security claim)."""
    classes = sum(any(c in group for c in password) for group in
                  (string.ascii_lowercase, string.ascii_uppercase, string.digits, string.punctuation))
    score = (len(password) >= 8) + (len(password) >= 12) + (len(password) >= 16) + (classes >= 3)
    return min(4, score if classes > 1 else min(score, 1))
