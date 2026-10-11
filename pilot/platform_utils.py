"""Small cross-platform helpers (Linux + Windows, with macOS as a bonus)."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from typing import List


def current_os() -> str:
    system = platform.system().lower()
    if system.startswith("win"):
        return "windows"
    if system == "darwin":
        return "macos"
    return "linux"


def is_windows() -> bool:
    return current_os() == "windows"


def is_linux() -> bool:
    return current_os() == "linux"


def has_display() -> bool:
    """Whether a GUI display is available for desktop (mouse/keyboard) control."""
    if is_windows() or current_os() == "macos":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def open_app(name: str) -> str:
    """Launch an application by name/path, using the OS launcher."""
    osname = current_os()
    try:
        if osname == "windows":
            os.startfile(name)  # type: ignore[attr-defined]
        elif osname == "macos":
            subprocess.Popen(["open", "-a", name])
        else:
            if shutil.which(name):
                subprocess.Popen([name])
            elif shutil.which("gtk-launch"):
                subprocess.Popen(["gtk-launch", name])
            else:
                subprocess.Popen(["xdg-open", name])
        return f"Launched {name!r}."
    except Exception as exc:  # pragma: no cover - platform dependent
        return f"Failed to launch {name!r}: {exc}"


def platform_summary() -> str:
    bits: List[str] = [f"OS={current_os()}", f"display={'yes' if has_display() else 'no'}"]
    return ", ".join(bits)
