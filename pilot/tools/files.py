"""Basic file-system helpers."""
from __future__ import annotations

import os
from pathlib import Path


def read_file(path: str, max_chars: int = 20_000) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        return f"No such file: {p}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"Could not read {p}: {exc}"
    if len(text) > max_chars:
        return text[:max_chars] + f"\n... (truncated, {len(text)} chars total)"
    return text


def write_file(path: str, content: str) -> str:
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Wrote {len(content)} chars to {p}."


def list_dir(path: str = ".") -> str:
    p = Path(path).expanduser()
    if not p.is_dir():
        return f"Not a directory: {p}"
    entries = []
    for entry in sorted(os.scandir(p), key=lambda e: (not e.is_dir(), e.name.lower())):
        marker = "/" if entry.is_dir() else ""
        entries.append(f"  {entry.name}{marker}")
    return f"{p}:\n" + "\n".join(entries[:300])
