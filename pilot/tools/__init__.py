"""Tool registry: the actions the model can take, and their JSON schemas.

Each tool is a name, a description, a JSON-schema for its arguments, a
handler, and a flag saying whether it needs confirmation when
``auto_approve`` is off.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..perception import grab_screen
from ..platform_utils import open_app
from .browser import BrowserController
from .computer import DesktopController
from .files import list_dir, read_file, write_file
from .shell import run_command


@dataclass
class ToolResult:
    text: str
    image_png: Optional[bytes] = None


class ToolContext:
    """Shared state for tools. Browser and desktop start lazily on first use."""

    def __init__(self, config) -> None:
        self.config = config
        self._browser: Optional[BrowserController] = None
        self._desktop: Optional[DesktopController] = None

    @property
    def browser(self) -> BrowserController:
        if self._browser is None:
            self._browser = BrowserController(self.config.browser, self.config)
        return self._browser

    @property
    def desktop(self) -> DesktopController:
        if self._desktop is None:
            self._desktop = DesktopController()
        return self._desktop

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()


@dataclass
class Tool:
    name: str
    description: str
    parameters: Dict[str, Any]
    handler: Callable[..., ToolResult]
    needs_confirm: bool = False

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _obj(props: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


_INT = {"type": "integer"}
_STR = {"type": "string"}
_BOOL = {"type": "boolean"}


def _secret_value(ctx: ToolContext, name: str, field: str) -> str:
    entry = ctx.config.secret(name)
    if field in entry:
        return entry[field]
    if "value" in entry:
        return entry["value"]
    raise KeyError(f"Secret {name!r} has no field {field!r} (has: {sorted(entry)})")


def build_tools(ctx: ToolContext) -> List[Tool]:
    T = ToolResult

    tools: List[Tool] = [
        # ---------------- browser (fast path for anything on the web) ------
        Tool(
            "browser_goto", "Open a URL in the automated browser.",
            _obj({"url": _STR}, ["url"]),
            lambda url: T(ctx.browser.goto(url) + "\n" + ctx.browser.snapshot()),
        ),
        Tool(
            "browser_snapshot",
            "List the interactive elements on the current page with their [ref] numbers.",
            _obj({}),
            lambda: T(ctx.browser.snapshot()),
        ),
        Tool(
            "browser_click", "Click the element with the given ref from the last snapshot.",
            _obj({"ref": _INT}, ["ref"]),
            lambda ref: T(ctx.browser.click_ref(ref) + "\n" + ctx.browser.snapshot()),
        ),
        Tool(
            "browser_type", "Type text into the element with the given ref. Set submit=true to press Enter after.",
            _obj({"ref": _INT, "text": _STR, "submit": _BOOL}, ["ref", "text"]),
            lambda ref, text, submit=False: T(
                ctx.browser.type_ref(ref, text, submit)
                + ("\n" + ctx.browser.snapshot() if submit else "")
            ),
        ),
        Tool(
            "browser_fill_secret",
            "Fill a field with a stored secret (e.g. a saved login) WITHOUT seeing its value. "
            "`name` is the secret's name, `field` is 'username', 'password' or 'value'.",
            _obj({"ref": _INT, "name": _STR, "field": _STR, "submit": _BOOL}, ["ref", "name", "field"]),
            lambda ref, name, field, submit=False: T(
                ctx.browser.fill_secret_ref(ref, _secret_value(ctx, name, field), submit)
                + ("\n" + ctx.browser.snapshot() if submit else "")
            ),
        ),
        Tool(
            "browser_press", "Press a key in the browser (e.g. Enter, Tab, Escape, Control+a).",
            _obj({"key": _STR}, ["key"]),
            lambda key: T(ctx.browser.press(key)),
        ),
        Tool(
            "browser_scroll", "Scroll the page vertically by dy pixels (negative scrolls up).",
            _obj({"dy": _INT}),
            lambda dy=600: T(ctx.browser.scroll(dy) + "\n" + ctx.browser.snapshot()),
        ),
        Tool(
            "browser_read", "Return the visible text content of the current page.",
            _obj({}),
            lambda: T(ctx.browser.get_text()),
        ),
        Tool(
            "browser_screenshot", "Take a screenshot of the browser page (use only when the snapshot is not enough).",
            _obj({}),
            lambda: T("Browser screenshot attached.", ctx.browser.screenshot_png()),
        ),
        Tool("browser_back", "Go back in history.", _obj({}), lambda: T(ctx.browser.back())),
        Tool("browser_forward", "Go forward in history.", _obj({}), lambda: T(ctx.browser.forward())),
        Tool("browser_reload", "Reload the page.", _obj({}), lambda: T(ctx.browser.reload())),
        Tool(
            "browser_new_tab", "Open a new tab, optionally at a URL.",
            _obj({"url": _STR}),
            lambda url=None: T(ctx.browser.new_tab(url)),
        ),
        Tool("browser_tabs", "List open tabs.", _obj({}), lambda: T(ctx.browser.list_tabs())),
        Tool(
            "browser_switch_tab", "Switch to the tab with the given index.",
            _obj({"index": _INT}, ["index"]),
            lambda index: T(ctx.browser.switch_tab(index) + "\n" + ctx.browser.snapshot()),
        ),
        Tool(
            "browser_wait_for_text", "Wait until the given text appears on the page.",
            _obj({"text": _STR, "timeout_ms": _INT}, ["text"]),
            lambda text, timeout_ms=10_000: T(ctx.browser.wait_for(text, timeout_ms)),
        ),

        # ---------------- desktop (native apps / anything outside the browser) --
        Tool(
            "screen_screenshot", "Take a screenshot of the whole desktop.",
            _obj({}),
            lambda: _desktop_shot(ctx),
        ),
        Tool(
            "mouse_click",
            "Click on the desktop at screen coordinates (x, y). button: left|right|middle; clicks: 1 or 2.",
            _obj({"x": _INT, "y": _INT, "button": _STR, "clicks": _INT}, ["x", "y"]),
            lambda x, y, button="left", clicks=1: T(ctx.desktop.click(x, y, button, clicks)),
        ),
        Tool(
            "mouse_move", "Move the mouse to (x, y).",
            _obj({"x": _INT, "y": _INT}, ["x", "y"]),
            lambda x, y: T(ctx.desktop.move(x, y)),
        ),
        Tool(
            "mouse_drag", "Drag from the current pointer position to (x, y).",
            _obj({"x": _INT, "y": _INT}, ["x", "y"]),
            lambda x, y: T(ctx.desktop.drag(x, y)),
        ),
        Tool(
            "mouse_scroll", "Scroll the mouse wheel (positive = up, negative = down).",
            _obj({"amount": _INT}, ["amount"]),
            lambda amount: T(ctx.desktop.scroll(amount)),
        ),
        Tool(
            "keyboard_type", "Type text with the keyboard into whatever has focus.",
            _obj({"text": _STR}, ["text"]),
            lambda text: T(ctx.desktop.type_text(text)),
        ),
        Tool(
            "keyboard_type_secret",
            "Type a stored secret into whatever has focus WITHOUT seeing its value.",
            _obj({"name": _STR, "field": _STR}, ["name", "field"]),
            lambda name, field: T(ctx.desktop.type_secret(_secret_value(ctx, name, field))),
        ),
        Tool(
            "keyboard_press", "Press one or more keys in sequence (e.g. ['enter'], ['tab','tab']).",
            _obj({"keys": {"type": "array", "items": _STR}}, ["keys"]),
            lambda keys: T(ctx.desktop.press(keys)),
        ),
        Tool(
            "keyboard_hotkey", "Press a key combination together (e.g. ['ctrl','c'] or ['alt','tab']).",
            _obj({"keys": {"type": "array", "items": _STR}}, ["keys"]),
            lambda keys: T(ctx.desktop.hotkey(keys)),
        ),
        Tool(
            "open_app", "Launch an application by name or path.",
            _obj({"name": _STR}, ["name"]),
            lambda name: T(open_app(name)),
        ),
        Tool(
            "wait", "Pause for a few seconds (max 10) to let something load.",
            _obj({"seconds": {"type": "number"}}, ["seconds"]),
            lambda seconds: T(ctx.desktop.wait(seconds)),
        ),

        # ---------------- system ----------------------------------------
        Tool(
            "shell", "Run a shell command (bash on Linux, PowerShell on Windows) and return its output.",
            _obj({"command": _STR, "timeout": _INT}, ["command"]),
            lambda command, timeout=120: T(run_command(command, timeout)),
            needs_confirm=True,
        ),
        Tool(
            "file_read", "Read a text file.",
            _obj({"path": _STR}, ["path"]),
            lambda path: T(read_file(path)),
        ),
        Tool(
            "file_write", "Write (overwrite) a text file.",
            _obj({"path": _STR, "content": _STR}, ["path", "content"]),
            lambda path, content: T(write_file(path, content)),
            needs_confirm=True,
        ),
        Tool(
            "file_list", "List a directory.",
            _obj({"path": _STR}),
            lambda path=".": T(list_dir(path)),
        ),
        Tool(
            "list_secrets", "List the NAMES of stored secrets (never their values).",
            _obj({}),
            lambda: T(", ".join(sorted(ctx.config.secrets)) or "(no secrets configured)"),
        ),
    ]
    return tools


def _desktop_shot(ctx: ToolContext) -> ToolResult:
    shot = grab_screen()
    w, h = ctx.desktop.size()
    return ToolResult(
        f"Desktop screenshot attached ({shot.width}x{shot.height} image of a {w}x{h} screen; "
        f"scale image coordinates by {w / max(shot.width, 1):.3f} to click).",
        shot.png,
    )
