"""Command-line entry point.

  pilot "open gmail and summarise my 5 newest emails"
  pilot -i                     # interactive chat, remembers context
  pilot --gui                  # small desktop window with a Stop button
  pilot --doctor               # check that everything is installed
"""
from __future__ import annotations

import argparse
import getpass
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict

from . import __version__
from .config import load_config
from .platform_utils import has_display, platform_summary


def _print(kind: str, text: str) -> None:
    prefix = {"say": "pilot>", "tool": "  ->", "result": "    ", "info": "*"}.get(kind, "")
    print(f"{prefix} {text}", flush=True)


def _confirm(name: str, args: Dict[str, Any]) -> bool:
    if getattr(_confirm, "always", False):
        return True
    print(f"\n[confirm] Pilot wants to run {name}:")
    print("  " + json.dumps(args, ensure_ascii=False, indent=2).replace("\n", "\n  "))
    answer = input("Allow? [y/N/a=always this session] ").strip().lower()
    if answer == "a":
        _confirm.always = True  # type: ignore[attr-defined]
    return answer in {"y", "yes", "a"}


def _ask(question: str) -> str:
    print(f"\n[pilot asks] {question}")
    if any(w in question.lower() for w in ("password", "passcode", "pin")):
        return getpass.getpass("> ")
    return input("> ")


def _row(status: str, name: str, text: str) -> None:
    print(f"  {'[' + status + ']':<10}{name:<12}{text}")


def doctor(cfg) -> int:
    ok = True
    print(f"Pilot {__version__} -- {platform_summary()}")
    print(f"Python {sys.version.split()[0]}")
    for mod, why in [
        ("openai", "talk to the model"),
        ("playwright", "browser control"),
        ("pyautogui", "mouse & keyboard"),
        ("mss", "fast screenshots (optional)"),
        ("PIL", "image resizing"),
    ]:
        try:
            importlib.import_module(mod)
            _row("ok", mod, why)
        except Exception as exc:
            # pyautogui refuses to import without a screen; that's expected on a headless box.
            optional = "optional" in why or (mod == "pyautogui" and not has_display())
            ok = ok and optional
            _row("warn" if optional else "MISSING", mod, f"{why}: {exc.__class__.__name__}")
    if cfg.browser.executable_path:
        exists = Path(cfg.browser.executable_path).exists()
        ok = ok and exists
        _row("ok" if exists else "MISSING", "browser", cfg.browser.executable_path)
    elif cfg.browser.channel:
        _row("ok", "browser", f"installed {cfg.browser.channel} (channel)")
    else:
        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                path = Path(getattr(p, cfg.browser.engine).executable_path)
            if not path.exists():
                raise FileNotFoundError(path)
            _row("ok", "browser", f"{cfg.browser.engine} at {path}")
        except Exception as exc:
            _row("warn", "browser", f"run `python -m playwright install {cfg.browser.engine}` ({exc.__class__.__name__})")
    if has_display():
        _row("ok", "display", "available")
    else:
        _row("warn", "display", "none -- desktop control disabled, browser can run headless")
    n_keys = len(cfg.api_keys)
    if n_keys:
        _row("ok", "api key", f"{n_keys} key(s) set" + (" (with failover)" if n_keys > 1 else ""))
    else:
        _row("warn", "api key", "not set (fine for local models, required for hosted APIs)")
    print(f"  model={cfg.model}  base_url={cfg.base_url}")
    print(f"  secrets: {', '.join(sorted(cfg.secrets)) or '(none)'}")
    return 0 if ok else 1


def _run_task(agent, task: str) -> bool:
    """Run one task, turning model/API failures into a one-line message."""
    try:
        _print("say", agent.run(task))
        return True
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        _print("info", f"error: {type(exc).__name__}: {exc}")
        return False


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pilot", description="AI agent that operates your computer.")
    p.add_argument("task", nargs="*", help="what you want done")
    p.add_argument("-i", "--interactive", action="store_true", help="chat mode, keeps context between tasks")
    p.add_argument("--gui", action="store_true", help="open the desktop window")
    p.add_argument("--serve", action="store_true", help="run the web control panel (drive it from your phone)")
    p.add_argument("--host", help="server bind address (use 0.0.0.0 to reach it over the network)")
    p.add_argument("--port", type=int, help="server port (default 8080)")
    p.add_argument("--token", help="server access token (auto-generated if omitted)")
    p.add_argument("--doctor", action="store_true", help="check installation and exit")
    p.add_argument("--config", help="path to config.toml")
    p.add_argument("--model", help="model name, e.g. gpt-4o or gemini-2.0-flash")
    p.add_argument("--base-url", dest="base_url", help="OpenAI-compatible endpoint")
    p.add_argument("-y", "--yes", dest="auto_approve", action="store_true", default=None,
                   help="never ask for confirmation before shell commands / file writes")
    p.add_argument("--max-steps", dest="max_steps", type=int)
    p.add_argument("--engine", choices=["chromium", "firefox", "webkit"])
    p.add_argument("--channel", help="use an installed browser: chrome, msedge, chrome-beta ...")
    p.add_argument("--browser-path", dest="executable_path",
                   help="path to any Chromium-based (Brave, Vivaldi, Opera...) or Firefox executable")
    p.add_argument("--profile", dest="user_data_dir", help="browser profile dir (keeps you logged in)")
    p.add_argument("--headless", action="store_true", default=None)
    p.add_argument("--print-config", action="store_true", help="show resolved config (secrets hidden)")
    p.add_argument("--version", action="version", version=f"pilot {__version__}")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    overrides = {k: getattr(args, k) for k in (
        "model", "base_url", "auto_approve", "max_steps", "engine", "channel", "executable_path",
        "user_data_dir", "headless")}
    cfg = load_config(args.config, overrides)
    if args.host is not None:
        cfg.server.host = args.host
    if args.port is not None:
        cfg.server.port = args.port
    if args.token is not None:
        cfg.server.token = args.token

    if args.print_config:
        print(json.dumps(cfg.redacted(), indent=2))
        return 0
    if args.doctor:
        return doctor(cfg)
    if args.serve:
        from .server import run_server

        return run_server(cfg)
    if args.gui:
        from .gui import run_gui

        return run_gui(cfg)

    from .agent import Agent

    agent = Agent(cfg, confirm=_confirm, ask_user=_ask, on_event=_print)
    try:
        if args.task and not args.interactive:
            return 0 if _run_task(agent, " ".join(args.task)) else 1
        print("Pilot interactive mode. Type a task, or 'exit'. Ctrl+C stops the current task.")
        if args.task:
            _run_task(agent, " ".join(args.task))
        while True:
            try:
                task = input("\nyou> ").strip()
            except EOFError:
                break
            if task.lower() in {"exit", "quit", ":q"}:
                break
            if not task:
                continue
            try:
                _run_task(agent, task)
            except KeyboardInterrupt:
                _print("info", "Task interrupted.")
        return 0
    except KeyboardInterrupt:
        _print("info", "Interrupted.")
        return 130
    finally:
        agent.close()


if __name__ == "__main__":
    sys.exit(main())
