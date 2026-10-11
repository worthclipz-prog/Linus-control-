"""Desktop control: mouse, keyboard and screen, via pyautogui.

This is the "look at the pixels and act" path used for native apps and the
OS shell. For anything on the web, the browser tools are faster and more
reliable, so the agent prefers those.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple


class DesktopController:
    def __init__(self) -> None:
        self._pg = None

    @property
    def pg(self):
        if self._pg is None:
            import pyautogui  # type: ignore

            pyautogui.FAILSAFE = True   # slam mouse to a corner to abort
            pyautogui.PAUSE = 0.02      # keep it snappy
            self._pg = pyautogui
        return self._pg

    def size(self) -> Tuple[int, int]:
        return tuple(self.pg.size())  # type: ignore[return-value]

    def position(self) -> Tuple[int, int]:
        return tuple(self.pg.position())  # type: ignore[return-value]

    def move(self, x: int, y: int, duration: float = 0.15) -> str:
        self.pg.moveTo(x, y, duration=duration)
        return f"Moved pointer to ({x}, {y})."

    def click(self, x: Optional[int] = None, y: Optional[int] = None,
              button: str = "left", clicks: int = 1) -> str:
        if x is not None and y is not None:
            self.pg.click(x=x, y=y, button=button, clicks=clicks, interval=0.03)
            return f"Clicked {button} x{clicks} at ({x}, {y})."
        self.pg.click(button=button, clicks=clicks, interval=0.03)
        return f"Clicked {button} x{clicks} at current position."

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> str:
        return self.click(x, y, clicks=2)

    def right_click(self, x: Optional[int] = None, y: Optional[int] = None) -> str:
        return self.click(x, y, button="right")

    def drag(self, x: int, y: int, duration: float = 0.3, button: str = "left") -> str:
        self.pg.dragTo(x, y, duration=duration, button=button)
        return f"Dragged to ({x}, {y})."

    def type_text(self, text: str, interval: float = 0.01) -> str:
        self.pg.typewrite(text, interval=interval)
        return f"Typed {len(text)} characters."

    def type_secret(self, value: str) -> str:
        """Type a secret value. The value is never returned or logged."""
        self.pg.typewrite(value, interval=0.01)
        return f"Typed a secret value ({len(value)} characters)."

    def press(self, keys: List[str]) -> str:
        for key in keys:
            self.pg.press(key)
        return f"Pressed: {', '.join(keys)}."

    def hotkey(self, keys: List[str]) -> str:
        self.pg.hotkey(*keys)
        return f"Hotkey: {'+'.join(keys)}."

    def scroll(self, amount: int) -> str:
        self.pg.scroll(amount)
        return f"Scrolled {amount}."

    def wait(self, seconds: float) -> str:
        time.sleep(max(0.0, min(seconds, 10.0)))
        return f"Waited {seconds}s."
