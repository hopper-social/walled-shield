import pytest

from walled_guard import dialogs


def test_prompt_message_mentions_kind_and_preview():
    head, body = dialogs.Request("prompt", "ChatGPT", ["my WALLED_SENSITIVE key"]).message()
    assert "text prompt" in head and "ChatGPT" in head and "my WALLED_SENSITIVE key" in body


def test_file_message_lists_names():
    head, body = dialogs.Request("file", "Perplexity", ["a.pdf — page 2", "b.png — image text (OCR)"]).message()
    assert "2 files" in head and "a.pdf" in body and "b.png" in body


def test_preview_centres_on_marker():
    text = "x" * 500 + " WALLED_SENSITIVE " + "y" * 500
    p = dialogs.preview(text, 80)
    assert "WALLED_SENSITIVE" in p and len(p) <= 84


def test_macos_script_defaults_to_cancel():
    script = dialogs.macos_script(dialogs.Request("file", "ChatGPT", ['quote"s.pdf — page 1']))
    assert 'default button "Cancel"' in script and 'cancel button "Cancel"' in script
    assert 'giving up after 120' in script and '\\"' in script                   # quotes escaped


def test_linux_command_defaults_to_cancel(monkeypatch):
    monkeypatch.setattr(dialogs.shutil, "which", lambda name: "/usr/bin/zenity" if name == "zenity" else None)
    cmd = dialogs.linux_command(dialogs.Request("prompt", "ChatGPT", ["<b>x</b> WALLED_SENSITIVE"]))
    assert cmd[0] == "zenity" and "--default-cancel" in cmd and "&lt;b&gt;" in " ".join(cmd)


@pytest.mark.parametrize("value, expected", [("continue", "continue"), ("cancel", "cancel"), ("weird", "cancel")])
def test_auto_answer(monkeypatch, value, expected):
    monkeypatch.setenv("WALLED_GUARD_AUTO_ANSWER", value)
    assert dialogs.ask(dialogs.Request("prompt", "ChatGPT", ["x"])) == expected


def test_backend_failure_means_cancel(monkeypatch):
    monkeypatch.delenv("WALLED_GUARD_AUTO_ANSWER", raising=False)
    monkeypatch.setattr(dialogs, "_backend", lambda: (lambda req: 1 / 0), raising=False)
    monkeypatch.setattr(dialogs.sys, "platform", "plan9")
    assert dialogs.ask(dialogs.Request("prompt", "ChatGPT", ["x"])) == "cancel"


def test_xmessage_fallback_never_treats_timeout_as_continue(monkeypatch):
    monkeypatch.setattr(dialogs.shutil, "which", lambda name: "/usr/bin/xmessage" if name == "xmessage" else None)
    cmd = dialogs.linux_command(dialogs.Request("prompt", "ChatGPT", ["x"]))
    assert cmd[0] == "xmessage" and "Cancel:1,Continue:10" in cmd

    import threading
    codes = iter([0, 10])                   # 0 is what xmessage returns on timeout
    monkeypatch.setattr(dialogs, "_run", lambda cmd, cancel: (next(codes), ""))
    assert dialogs._linux(dialogs.Request("prompt", "ChatGPT", ["x"]), threading.Event()) == "cancel"
    assert dialogs._linux(dialogs.Request("prompt", "ChatGPT", ["x"]), threading.Event()) == "continue"


def test_cancel_kills_dialog_program():
    import threading, time
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    start = time.monotonic()
    code, _ = dialogs._run(["sleep", "30"], cancel)
    assert code == -1 and time.monotonic() - start < 3


def test_rule_names_in_message():
    head, body = dialogs.Request("prompt", "ChatGPT", ["x"], rules=["Payment card number"], phone=True).message()
    assert head.startswith("Sensitive data") and "Payment card number" in body and "Telegram" in body


def test_no_backend_is_reported(monkeypatch):
    monkeypatch.delenv("WALLED_GUARD_AUTO_ANSWER", raising=False)
    monkeypatch.setattr(dialogs.sys, "platform", "linux")
    monkeypatch.setattr(dialogs.shutil, "which", lambda name: None)
    monkeypatch.setattr(dialogs, "tk_available", lambda: False)
    assert dialogs.backend_name().startswith("NONE")


def test_macos_alert_script_escapes_and_defaults_to_cancel():
    req = dialogs.Request("prompt", "ChatGPT", ['say "hi"\n</script> WALLED_SENSITIVE'], rules=["WALLED_SENSITIVE marker"])
    script = dialogs.macos_alert_script(req)
    assert "addButtonWithTitle('Cancel');\nvar go = a.addButtonWithTitle('Continue')" in script   # Cancel = default
    assert '\\"hi\\"' in script and "r == 1001 ? 'continue' : 'cancel'" in script


def test_macos_falls_back_to_applescript(monkeypatch):
    import threading
    calls = []

    def fake_run(cmd, cancel):
        calls.append(cmd)
        return (1, "") if "JavaScript" in cmd else (0, "button returned:Continue, gave up:false")
    monkeypatch.setattr(dialogs, "_run", fake_run)
    assert dialogs._macos(dialogs.Request("prompt", "ChatGPT", ["x"]), threading.Event()) == "continue"
    assert len(calls) == 2


def test_parts_for_files_and_fill():
    head, info, detail = dialogs.parts(dialogs.Request("file", "Perplexity", ["a.pdf — page 2", "b.png — OCR"], rules=["Card"]))
    assert "2 files" in head and "a.pdf" in detail and "Card" in detail
    head, _, detail = dialogs.parts(dialogs.Request("fill", "github.com", ["me@x.com"]))
    assert "github.com" in head and "me@x.com" in detail
