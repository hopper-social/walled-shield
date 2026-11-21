"""Small, usable stdio MCP server with two tools scoped to a private notes directory."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import secrets
import stat
import sys
from contextlib import contextmanager
from typing import Any, Iterator

try:
    from .html_files import TOOLS as HTML_TOOLS, call_tool as call_html_tool
except ImportError:
    from html_files import TOOLS as HTML_TOOLS, call_tool as call_html_tool  # type: ignore[import-not-found,no-redef]  # run as a script

MAX_NOTE = 256 * 1024
NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
TOOLS = [
    {"name": "notes_write", "description": "Create or replace a text note in the configured directory.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Note name, letters/numbers/_/- only"},
         "text": {"type": "string", "description": "UTF-8 note content (up to 256 KiB)"}},
         "required": ["name", "text"], "additionalProperties": False}},
    {"name": "notes_read", "description": "Read a text note from the configured directory.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string", "description": "Note name, letters/numbers/_/- only"}},
         "required": ["name"], "additionalProperties": False}},
]


def note_path(directory: pathlib.Path, name: object) -> pathlib.Path:
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("Note name must be 1-64 letters, digits, underscores or hyphens")
    return directory / (name + ".txt")


@contextmanager
def _open_notes(directory: pathlib.Path) -> Iterator[int]:
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ValueError("Notes directory must be a directory")
        yield fd
    finally:
        os.close(fd)


def call_tool(directory: pathlib.Path, name: object, args: object) -> dict[str, Any]:
    try:
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object")
        filename = note_path(directory, args.get("name")).name
        if name == "notes_write":
            if set(args) != {"name", "text"}:
                raise ValueError("Unexpected tool arguments")
            text = args["text"]
            if not isinstance(text, str):
                raise ValueError("Text must be UTF-8 and no larger than 256 KiB")
            data = text.encode("utf-8")
            if len(data) > MAX_NOTE:
                raise ValueError("Text must be UTF-8 and no larger than 256 KiB")
            with _open_notes(directory) as folder:
                temporary = ".note-" + secrets.token_hex(16)
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                             0o600, dir_fd=folder)
                try:
                    with os.fdopen(fd, "wb") as output:
                        output.write(data)
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, filename, src_dir_fd=folder, dst_dir_fd=folder)
                    os.fsync(folder)
                finally:
                    try:
                        os.unlink(temporary, dir_fd=folder)
                    except FileNotFoundError:
                        pass
            return {"content": [{"type": "text", "text": "Note saved"}]}
        if name == "notes_read":
            if set(args) != {"name"}:
                raise ValueError("Unexpected tool arguments")
            with _open_notes(directory) as folder:
                fd = os.open(filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=folder)
                with os.fdopen(fd, "rb") as source:
                    info = os.fstat(source.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_NOTE:
                        raise ValueError("Note must be regular and at most 256 KiB")
                    content = source.read(MAX_NOTE + 1)
            if len(content) > MAX_NOTE:
                raise ValueError("Note exceeds 256 KiB")
            return {"content": [{"type": "text", "text": content.decode("utf-8")}]}
        raise ValueError("Unknown tool")
    except (OSError, UnicodeError, ValueError):
        return {"content": [{"type": "text", "text": "Note operation refused or unavailable"}], "isError": True}


def _error(request_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def process(message: dict[str, Any], directory: pathlib.Path,
            html_directory: pathlib.Path | None = None) -> dict[str, Any] | None:
    request_id = message.get("id")
    valid_id = type(request_id) in (str, int)
    if (message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str)
            or ("id" in message and not valid_id)
            or ("params" in message and not isinstance(message["params"], dict))):
        return _error(request_id if valid_id else None, -32600, "Invalid Request")
    if "id" not in message:
        return None
    method = message["method"]
    params = message.get("params", {})
    response = {"jsonrpc": "2.0", "id": request_id}
    if method == "initialize":
        if (not isinstance(params.get("protocolVersion"), str)
                or not isinstance(params.get("capabilities"), dict)
                or not isinstance(params.get("clientInfo"), dict)):
            return _error(request_id, -32602, "Invalid initialize parameters")
        response["result"] = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "approval-gate-notes", "version": "1.0.0"}}
    elif method == "ping":
        response["result"] = {}
    elif method == "tools/list":
        if set(params) - {"cursor"} or params.get("cursor") is not None:
            return _error(request_id, -32602, "Invalid tools/list parameters")
        response["result"] = {"tools": TOOLS + HTML_TOOLS if html_directory is not None else TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        if set(params) - {"name", "arguments", "_meta"} or not isinstance(name, str) or not isinstance(args, dict):
            return _error(request_id, -32602, "Invalid tools/call parameters")
        if html_directory is not None and name in ("html_list", "html_read"):
            response["result"] = call_html_tool(html_directory, name, args)
        else:
            response["result"] = call_tool(directory, name, args)
    else:
        response["error"] = {"code": -32601, "message": "Method not found"}
    return response


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise ValueError("Non-JSON numeric constant")


def main() -> int:
    parser = argparse.ArgumentParser(description="Local MCP text notes server")
    parser.add_argument("--directory", type=pathlib.Path, required=True,
                        help="existing private directory holding notes")
    parser.add_argument("--html-directory", type=pathlib.Path,
                        help="optional existing folder of top-level HTML files; enables approval-gated read-only tools")
    args = parser.parse_args()
    directory = args.directory.absolute()
    if directory.is_symlink() or not directory.is_dir():
        parser.error("--directory must be an existing, non-symlink directory")
    html_directory = args.html_directory.absolute() if args.html_directory is not None else None
    if html_directory is not None and (html_directory.is_symlink() or not html_directory.is_dir()):
        parser.error("--html-directory must be an existing, non-symlink directory")
    while line := sys.stdin.buffer.readline(1024 * 1024 + 2):
        if len(line) > 1024 * 1024 or not line.endswith(b"\n"):
            print("Invalid oversized MCP message", file=sys.stderr)
            return 1
        response: dict[str, Any] | None
        try:
            message = json.loads(line, object_pairs_hook=_unique_pairs, parse_constant=_invalid_constant)
        except (ValueError, UnicodeError, RecursionError):
            response = _error(None, -32700, "Parse error")
        else:
            response = (process(message, directory, html_directory) if isinstance(message, dict)
                        else _error(None, -32600, "Invalid Request"))
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
