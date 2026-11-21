"""Topmost local approval window (``--approver popup``).

Tk is not thread-safe, so every Tk call runs on the main thread: worker threads enqueue a
request and wait on a private one-slot queue, and a 50 ms Tk timer shows one window at a time
above all other applications. Timeout, cancellation, window close or shutdown all answer
Block. Requests too large to preview in full are blocked rather than shown partially.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Tuple

try:                                    # Tk is optional: only the popup approver needs it
    import tkinter as tk
    from tkinter import scrolledtext
except ImportError:                     # pragma: no cover - depends on the Python build
    tk = None  # type: ignore[assignment]
    scrolledtext = None  # type: ignore[assignment]

from .gate import Request, compact_request


MAX_PREVIEW_CHARS = 16000
MAX_LABEL_CHARS = 256

Labels = Tuple[str, str, str, str]          # server, method, tool, request id (JSON-quoted)
Preview = Tuple[Labels, str]                # labels and pretty-printed parameters


def action_description(method: str, tool_label: str) -> str:
    if method == "tools/call":
        return f"Claude wants to run tool {tool_label}."
    if method == "resources/read":
        return "Claude wants to read a resource from this server."
    if method == "prompts/get":
        return "Claude wants to retrieve a prompt from this server."
    return "Claude wants to send this request to the server."


def approval_preview(request: Request) -> tuple[tuple[str, str, str, str], str] | None:
    tool = (request.parameters.get("name") if request.method == "tools/call" and isinstance(request.parameters, dict) else None)
    server, method, tool_text, request_id = (json.dumps(value, ensure_ascii=True) for value in
                                             (request.server, request.method, tool, request.request_id))
    if tool is None:
        tool_text = "(unspecified)" if request.method == "tools/call" else "(not a tool call)"
    labels: Labels = (server, method, tool_text, request_id)
    details = compact_request(request)
    if len(details) > MAX_PREVIEW_CHARS or any(len(label) > MAX_LABEL_CHARS for label in labels):
        return None
    return labels, details


class PopupApprover:
    def __init__(self, timeout: int = 60, stop_event: threading.Event | None = None) -> None:
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("MCP Approval")
        self.timeout = timeout
        self.stop_event = stop_event
        self.pending: queue.Queue[tuple[Request, queue.Queue[bool], float, Preview]] = queue.Queue()
        # (window, reply queue, deadline, request) of the window currently shown
        self.active: tuple[tk.Toplevel, queue.Queue[bool], float, Request] | None = None
        self.closed = threading.Event()
        self.root.after(50, self._tick)

    def decide(self, request: Request) -> bool:
        if self.closed.is_set():
            return False
        preview = approval_preview(request)
        if preview is None:
            return False
        response: queue.Queue[bool] = queue.Queue(maxsize=1)
        deadline = time.monotonic() + self.timeout
        self.pending.put((request, response, deadline, preview))
        while not self.closed.is_set() and (request.cancelled is None or not request.cancelled.is_set()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                return bool(response.get(timeout=min(remaining, 0.05)))
            except queue.Empty:
                continue
        return False

    def _tick(self) -> None:
        if self.closed.is_set():
            return
        if self.stop_event is not None and self.stop_event.is_set():
            self.stop()
            return
        if self.active is None:
            try:
                request, response, deadline, preview = self.pending.get_nowait()
            except queue.Empty:
                pass
            else:
                if time.monotonic() >= deadline or (request.cancelled is not None and request.cancelled.is_set()):
                    try:
                        response.put_nowait(False)
                    except queue.Full:
                        pass
                else:
                    try:
                        self._show(request, response, deadline, preview)
                    except tk.TclError:
                        response.put_nowait(False)
                        self.stop()
                        return
        elif (time.monotonic() >= self.active[2]
              or (self.active[3].cancelled is not None and self.active[3].cancelled.is_set())):
            self._finish(False)
        self.root.after(50, self._tick)

    def _show(self, request: Request, response: queue.Queue[bool], deadline: float,
              preview: Preview) -> None:
        (server_label, method_label, tool_label, id_label), details_text = preview
        window = tk.Toplevel(self.root)
        window.title("Approve MCP request?")
        window.geometry("620x530")
        window.attributes("-topmost", True)
        window.lift()
        window.protocol("WM_DELETE_WINDOW", lambda: self._finish(False))
        tk.Label(window, text="MCP request awaiting approval", font=("Arial", 16, "bold")).pack(pady=10)
        tk.Label(window, text=action_description(request.method, tool_label), font=("Arial", 13, "bold"),
                 justify="left", anchor="w", wraplength=580).pack(fill="x", padx=15, pady=(0, 8))
        tk.Label(window, text=f"Configured server: {server_label}\nMethod: {method_label}\nTool: {tool_label}\nRequest ID: {id_label}",
                 justify="left", anchor="w", wraplength=580).pack(fill="x", padx=15)
        tk.Label(window, text="Allow sends the request to the server; Block denies it. The tool's effects cannot be verified here.",
                 justify="left", anchor="w", wraplength=580).pack(fill="x", padx=15, pady=(8, 0))
        tk.Label(window, text="This is not a macOS or Claude screen/file-sharing permission. Those must be handled in their own prompts.",
                 justify="left", anchor="w", wraplength=580).pack(fill="x", padx=15, pady=(5, 0))
        tk.Label(window, text="Parameters (may contain private information):", anchor="w").pack(fill="x", padx=15, pady=(10, 0))
        details = scrolledtext.ScrolledText(window, wrap="word", height=12)
        details.pack(fill="both", expand=True, padx=15, pady=5)
        details.insert("1.0", details_text)
        details.configure(state="disabled")
        buttons = tk.Frame(window)
        buttons.pack(pady=12)
        tk.Button(buttons, text="Block", width=14, command=lambda: self._finish(False)).pack(side="left", padx=12)
        tk.Button(buttons, text="Allow", width=14, command=lambda: self._finish(True)).pack(side="left", padx=12)
        self.active = (window, response, deadline, request)
        try:
            window.focus_force()
            window.grab_set()
        except tk.TclError:
            pass

    def _finish(self, allowed: bool) -> None:
        if self.active is None:
            return
        window, response, _, _ = self.active
        self.active = None
        try:
            response.put_nowait(allowed)
        except queue.Full:
            pass
        try:
            window.grab_release()
            window.destroy()
        except tk.TclError:
            pass

    def stop(self) -> None:
        self.closed.set()
        self._finish(False)
        while True:
            try:
                _, response, _, _ = self.pending.get_nowait()
                response.put_nowait(False)
            except queue.Empty:
                break
        self.root.quit()

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self.stop()
            self.root.destroy()
