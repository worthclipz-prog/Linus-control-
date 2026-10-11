"""Run shell commands on the host, cross-platform."""
from __future__ import annotations

import subprocess
from ..platform_utils import is_windows


def run_command(command: str, timeout: int = 120, cwd: str | None = None) -> str:
    """Execute a shell command and capture its output."""
    try:
        if is_windows():
            args = ["powershell", "-NoProfile", "-Command", command]
        else:
            args = ["/bin/bash", "-lc", command]
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        parts = [f"exit_code={proc.returncode}"]
        if out:
            parts.append("stdout:\n" + out[:8000])
        if err:
            parts.append("stderr:\n" + err[:4000])
        return "\n".join(parts)
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."
    except Exception as exc:  # pragma: no cover
        return f"Command failed to start: {exc}"
