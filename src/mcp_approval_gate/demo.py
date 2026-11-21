"""Scripted Tk demonstration with a real file-writing MCP notes server."""

from __future__ import annotations

import io
import json
import pathlib
import sys
import tempfile
import threading
import tkinter as tk

from .gate import ApprovalGate, Request
from .popup import PopupApprover
from .stdio_proxy import serve


def main() -> int:
    approver = PopupApprover(timeout=20)
    temporary = tempfile.TemporaryDirectory(prefix="mcp-notes-demo-")
    directory = pathlib.Path(temporary.name)
    commands = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "notes_write", "arguments": {"name": "approved", "text": "Saved by the real MCP server"}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "notes_write", "arguments": {"name": "blocked", "text": "Must never be saved"}}},
    ]
    first_done = threading.Event()

    class SequencedInput:
        index = 0

        def readline(self, _limit: int, /) -> bytes:
            if self.index == 1:
                if not first_done.wait(15):
                    raise TimeoutError("first demo approval timed out")
            if self.index == len(commands):
                return b""
            item = commands[self.index]
            self.index += 1
            return (json.dumps(item) + "\n").encode()

    input_stream = SequencedInput()
    output_stream = io.BytesIO()

    def decide(request: Request) -> bool:
        allowed = approver.decide(request)
        if request.request_id == 1:
            first_done.set()
        return allowed
    notes_server = pathlib.Path(__file__).resolve().parent / "examples" / "notes_server.py"
    def run_server() -> None:
        serve([sys.executable, "-u", str(notes_server), "--directory", str(directory)],
              ApprovalGate(decide), "private notes", input_stream, output_stream)
        approver.root.after(0, approver.stop)

    threading.Thread(target=run_server, daemon=True).start()
    steps = [(1, "Allow"), (2, "Block")]
    current = 0

    def drive() -> None:
        nonlocal current
        if approver.closed.is_set() or current >= len(steps):
            return
        active = approver.active
        if active and active[0].title() == "Approve MCP request?":
            window = active[0]
            requested = steps[current][1]
            for frame in window.winfo_children():
                for button in frame.winfo_children():
                    if isinstance(button, tk.Button) and button.cget("text") == requested:
                        button.invoke()
                        break
            current += 1
            status = tk.Toplevel(approver.root)
            status.title(f"MCP demo result {current}")
            status.geometry("600x220")
            status.attributes("-topmost", True)
            tk.Label(status, text=f"Request {current}: {requested.upper()}", font=("Arial", 22, "bold")).pack(pady=24)
            tk.Label(status, text="Allowed requests write a note; blocked requests receive JSON-RPC -32001.",
                     wraplength=500).pack(pady=8)
            status.after(800, status.destroy)
        approver.root.after(1000, drive)

    approver.root.after(500, drive)
    approver.run()
    replies = [json.loads(line) for line in output_stream.getvalue().splitlines()]
    assert len(replies) == 2, replies
    assert next(item for item in replies if item["id"] == 1)["result"]["content"][0]["text"] == "Note saved"
    assert next(item for item in replies if item["id"] == 2)["error"]["code"] == -32001
    assert (directory / "approved.txt").read_text() == "Saved by the real MCP server"
    assert not (directory / "blocked.txt").exists()
    temporary.cleanup()
    print("PASS: approved note saved; blocked note absent; denial returned -32001")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
