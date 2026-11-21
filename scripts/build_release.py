"""Build everything a release needs into dist/:

  walled_shield-<v>-py3-none-any.whl   pip-installable app (includes the extension and UI)
  walled-shield-<v>.tar.gz             source distribution
  walled-extension-<v>.zip             the Chrome extension (Load unpacked / Chrome Web Store upload)
  walled-shield-<v>-installer.zip      double-click “Install Walled.command” (or bash install.sh) + wheel
  SHA256SUMS                           checksums for all of the above

    python scripts/build_release.py
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
sys.path.insert(0, str(ROOT / "src"))
from walled import __version__ as VERSION  # noqa: E402


def run(*cmd: str) -> None:
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> int:
    shutil.rmtree(DIST, ignore_errors=True)
    DIST.mkdir()
    run(sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-w", str(DIST), ".")
    shutil.rmtree(ROOT / "build", ignore_errors=True)       # or "python -m build" imports this folder
    try:
        run(sys.executable, "-m", "build", "--sdist", "--no-isolation", "--outdir", str(DIST))
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("(sdist skipped: install the 'build' package to produce it)")
    ext = DIST / f"walled-extension-{VERSION}.zip"
    source = ROOT / "src" / "walled" / "extension"
    with zipfile.ZipFile(ext, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(source.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                z.write(f, f.relative_to(source))
    wheel = next(DIST.glob("*.whl"))
    bundle = DIST / f"walled-shield-{VERSION}-installer.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        def executable(src: Path, name: str) -> None:
            info = zipfile.ZipInfo(f"walled-shield-{VERSION}/{name}")
            info.external_attr = 0o755 << 16                  # double-clickable after unzip
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, src.read_text())
        executable(ROOT / "install.sh", "install.sh")
        for launcher in ("Install Walled.command", "Uninstall Walled.command"):
            executable(ROOT / "packaging" / launcher, launcher)
        z.write(ROOT / "packaging" / "START HERE.txt", f"walled-shield-{VERSION}/START HERE.txt")
        z.write(wheel, f"walled-shield-{VERSION}/{wheel.name}")
        z.write(ROOT / "README.md", f"walled-shield-{VERSION}/README.md")
        z.write(ROOT / "LICENSE", f"walled-shield-{VERSION}/LICENSE")
    sums = []
    for f in sorted(DIST.iterdir()):
        if f.is_file() and f.name != "SHA256SUMS":
            sums.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.name}")
    (DIST / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print("\n".join(sums))
    for leftover in (ROOT / "build", *ROOT.glob("src/*.egg-info")):
        shutil.rmtree(leftover, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
