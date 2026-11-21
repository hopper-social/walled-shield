import base64
import io
import json
import struct

import pytest

from walled_guard import host


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WALLED_GUARD_HOME", str(tmp_path))
    monkeypatch.setenv("WALLED_GUARD_AUTO_ANSWER", "cancel")


def frame(msg):
    data = json.dumps(msg).encode()
    return struct.pack("<I", len(data)) + data


def run(*messages):
    out = io.BytesIO()
    code = host.serve(io.BytesIO(b"".join(frame(m) for m in messages)), out)
    out.seek(0)
    replies = []
    while header := out.read(4):
        replies.append(json.loads(out.read(struct.unpack("<I", header)[0])))
    return code, replies


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def test_ping_and_ids():
    code, [reply] = run({"type": "ping", "id": 7})
    assert code == 0 and reply["ok"] and reply["id"] == 7 and "version" in reply


def test_clean_prompt_is_allowed_without_dialog(tmp_path):
    _, [reply] = run({"type": "confirm_prompt", "site": "chatgpt", "text": "hello"})
    assert reply == {"ok": True, "found": False, "decision": "allow", "hits": []}
    assert not (tmp_path / "decisions.jsonl").exists()


def test_marked_prompt_asks(monkeypatch, tmp_path):
    monkeypatch.setenv("WALLED_GUARD_AUTO_ANSWER", "continue")
    _, [reply] = run({"type": "confirm_prompt", "site": "perplexity", "text": "x WALLED_SENSITIVE"})
    assert reply["decision"] == "continue"
    entry = json.loads((tmp_path / "decisions.jsonl").read_text())
    assert entry["site"] == "Perplexity" and "text" not in entry            # no content in the log


def test_files_mixed():
    _, [reply] = run({"type": "inspect_files", "site": "chatgpt", "files": [
        {"name": "a.txt", "data": b64(b"WALLED_SENSITIVE")},
        {"name": "b.txt", "data": b64(b"clean")},
    ]})
    assert reply["decision"] == "cancel"
    assert [r["found"] for r in reply["results"]] == [True, False]


def test_clean_files_allowed():
    _, [reply] = run({"type": "inspect_files", "site": "chatgpt", "files": [{"name": "b.txt", "data": b64(b"clean")}]})
    assert reply["decision"] == "allow"


def test_oversized_supported_file_asks_but_unsupported_passes():
    _, [reply] = run({"type": "inspect_files", "site": "chatgpt", "files": [{"name": "big.pdf", "skipped": "larger than 25 MB"}]})
    assert reply["decision"] == "cancel"
    _, [reply] = run({"type": "inspect_files", "site": "chatgpt", "files": [{"name": "movie.mp4", "skipped": "not a text, PDF or image file"}]})
    assert reply["decision"] == "allow"


@pytest.mark.parametrize("msg", [
    {"type": "confirm_prompt"},
    {"type": "inspect_files", "files": []},
    {"type": "nope"},
])
def test_bad_requests_fail_closed(msg):
    _, [reply] = run(msg)
    assert reply["ok"] is False


def test_bad_base64_is_reported():
    _, [reply] = run({"type": "inspect_files", "files": [{"name": "a.txt", "data": "!!!"}]})
    assert reply["results"][0]["error"]


def test_internal_error_keeps_bridge_alive(monkeypatch):
    monkeypatch.setattr(host.inspector, "inspect_prompt", lambda t: 1 / 0)
    code, replies = run({"type": "confirm_prompt", "text": "x"}, {"type": "ping"})
    assert replies[0]["ok"] is False and replies[1]["ok"] is True and code == 0


def test_oversized_frame_rejected():
    out = io.BytesIO()
    assert host.serve(io.BytesIO(struct.pack("<I", host.MAX_MESSAGE + 1)), out) == 1
