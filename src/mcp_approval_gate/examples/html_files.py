"""Read-only, non-recursive HTML tools for an explicitly configured local directory."""

from __future__ import annotations

import heapq
import os
import pathlib
import stat
from contextlib import contextmanager
from typing import Any, Iterator

MAX_HTML = 256 * 1024
MAX_RESULTS = 20
TOOLS = [
    {"name": "html_list", "description": "List up to 20 newest top-level .html files in the configured HTML folder (names, sizes and modification times only). Does not search the Mac or read file contents.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "html_read", "description": "Read one UTF-8 .html file by exact filename from the configured HTML folder, returning its content to Claude. Files over 256 KiB and symlinks are refused.",
     "inputSchema": {"type": "object", "properties": {
         "filename": {"type": "string", "description": "Exact top-level .html filename returned by html_list"}},
         "required": ["filename"], "additionalProperties": False}},
]


def _filename(value: object) -> str:
    if (not isinstance(value, str) or not value.lower().endswith(".html")
            or not value or value in (".", "..") or "/" in value or "\\" in value
            or "\x00" in value):
        raise ValueError("Expected a top-level .html filename")
    return value


@contextmanager
def _open_folder(directory: pathlib.Path) -> Iterator[int]:
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        yield fd
    finally:
        os.close(fd)


def call_tool(directory: pathlib.Path, name: str, args: object) -> dict[str, Any]:
    try:
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be an object")
        with _open_folder(directory) as folder:
            if name == "html_list":
                if args:
                    raise ValueError("Unexpected tool arguments")
                files: list[tuple[int, str, int]] = []  # min-heap of (mtime_ns, name, size)
                with os.scandir(folder) as entries:
                    for entry in entries:
                        if not entry.name.lower().endswith(".html"):
                            continue
                        try:
                            entry.name.encode("utf-8")
                        except UnicodeError:
                            continue
                        info = entry.stat(follow_symlinks=False)
                        if stat.S_ISREG(info.st_mode):
                            item = (info.st_mtime_ns, entry.name, info.st_size)
                            if len(files) < MAX_RESULTS:
                                heapq.heappush(files, item)
                            elif item > files[0]:
                                heapq.heapreplace(files, item)
                text = "\n".join(f"{filename} | {size} bytes | mtime_ns={modified}"
                                 for modified, filename, size in sorted(files, reverse=True))
                return {"content": [{"type": "text", "text": text or "No top-level HTML files found"}]}
            if name == "html_read":
                if set(args) != {"filename"}:
                    raise ValueError("Expected only filename")
                filename = _filename(args["filename"])
                fd = os.open(filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=folder)
                with os.fdopen(fd, "rb") as source:
                    info = os.fstat(source.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_HTML:
                        raise ValueError("HTML file must be regular and at most 256 KiB")
                    content = source.read(MAX_HTML + 1)
                if len(content) > MAX_HTML:
                    raise ValueError("HTML file exceeds 256 KiB")
                return {"content": [{"type": "text", "text": content.decode("utf-8")}]}
            raise ValueError("Unknown HTML tool")
    except (OSError, UnicodeError, ValueError) as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
