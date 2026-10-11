"""Screen perception: grab the screen as a PNG and as a data URL for the model.

Uses ``mss`` for fast capture when available, falling back to ``pyautogui``.
"""
from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class Screenshot:
    png: bytes
    width: int
    height: int

    def data_url(self) -> str:
        b64 = base64.b64encode(self.png).decode("ascii")
        return f"data:image/png;base64,{b64}"


def _downscale(png: bytes, max_width: int) -> Tuple[bytes, int, int]:
    """Shrink wide screenshots so the model sees them fast and cheap."""
    try:
        from PIL import Image
    except Exception:
        return png, 0, 0
    img = Image.open(io.BytesIO(png))
    w, h = img.size
    if w <= max_width:
        return png, w, h
    ratio = max_width / float(w)
    new_size = (max_width, int(h * ratio))
    img = img.convert("RGB").resize(new_size, Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue(), new_size[0], new_size[1]


def grab_screen(max_width: int = 1280, region: Optional[Tuple[int, int, int, int]] = None) -> Screenshot:
    """Capture the primary monitor (or a region: left, top, width, height)."""
    png: Optional[bytes] = None
    try:
        import mss  # type: ignore
        import mss.tools  # type: ignore

        with mss.mss() as sct:
            if region:
                left, top, width, height = region
                monitor = {"left": left, "top": top, "width": width, "height": height}
            else:
                monitor = sct.monitors[1]  # primary monitor
            shot = sct.grab(monitor)
            png = mss.tools.to_png(shot.rgb, shot.size)
    except Exception:
        png = None

    if png is None:
        # Fallback: pyautogui (works where mss is unavailable).
        import pyautogui  # type: ignore

        img = pyautogui.screenshot(region=region)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png = buf.getvalue()

    png, w, h = _downscale(png, max_width)
    if w == 0:
        # Pillow missing; report raw size via pyautogui if possible.
        try:
            import pyautogui  # type: ignore

            w, h = pyautogui.size()
        except Exception:
            w, h = (0, 0)
    return Screenshot(png=png, width=w, height=h)
