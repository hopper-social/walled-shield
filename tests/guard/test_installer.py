import json
import sys

import pytest

from walled_guard import installer

pytestmark = pytest.mark.skipif(sys.platform.startswith("win"), reason="file-based registration")


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    monkeypatch.setenv("WALLED_GUARD_HOME", str(tmp_path / "state"))
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_manifest_pins_extension():
    m = installer.manifest(installer.Path("/x/host"))
    assert m["name"] == "com.walled.guard" and m["type"] == "stdio"
    assert m["allowed_origins"] == ["chrome-extension://lbnpgknepeocidimhgmodenkcfngmkdj/"]


def test_install_only_into_existing_browsers(home):
    target = installer.targets()[0]
    target.manifest_dir.parent.mkdir(parents=True)
    done = installer.install()
    assert len(done) == 1
    written = json.loads((target.manifest_dir / "com.walled.guard.json").read_text())
    launcher = installer.Path(written["path"])
    assert launcher.exists() and launcher.stat().st_mode & 0o111
    assert "-m walled_guard host" in launcher.read_text()
    assert installer.installed() == [target.browser]
    assert installer.uninstall() and installer.installed() == []


def test_install_forced_browser(home):
    assert installer.install(browsers=["chromium"])
    assert "Chromium" in installer.installed()


def test_nothing_found(home):
    assert installer.install() == []


def test_host_accepts_browser_arguments(monkeypatch):
    from walled_guard import __main__ as cli, host
    monkeypatch.setattr(host, "serve", lambda: 0)          # argparse must not see Chrome's arguments
    for args in (["host", "chrome-extension://lbnpgknepeocidimhgmodenkcfngmkdj/"], ["host", "--parent-window=0", "chrome-extension://x/"]):
        assert cli.main(args) == 0


def test_doctor_launches_like_chrome(home):
    from walled_guard import doctor
    target = installer.targets()[0]
    target.manifest_dir.parent.mkdir(parents=True)
    installer.install()
    assert doctor.run() == 0


def test_doctor_reports_wrong_origin(home, capsys):
    from walled_guard import doctor
    target = installer.targets()[0]
    target.manifest_dir.parent.mkdir(parents=True)
    installer.install(extension_id="abcdefghijklmnopabcdefghijklmnop")
    assert doctor.run() == 1
    assert "access forbidden" in capsys.readouterr().out
