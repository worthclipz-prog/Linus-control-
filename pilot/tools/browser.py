"""Fast browser control via Playwright.

The agent works with the page's *structure*, not its pixels: ``snapshot()``
returns a compact list of interactive elements, each tagged with a stable
``ref`` number. The agent then acts on ``ref``s (click/type), which is far
faster and more reliable than locating things by screenshot coordinates.

Supports Chromium (incl. real Chrome/Edge via ``channel``) and Firefox.
A persistent ``user_data_dir`` keeps you logged in between runs.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, List, Optional

# JavaScript that tags every visible, interactive element with a ref and
# returns a compact description of each. Kept intentionally small and fast.
_SNAPSHOT_JS = r"""
() => {
  const sel = 'a,button,input,textarea,select,summary,[role=button],[role=link],[role=tab],[role=checkbox],[role=menuitem],[onclick],[contenteditable=""],[contenteditable=true]';
  const items = [];
  let ref = 0;
  const seen = new Set();
  for (const el of document.querySelectorAll(sel)) {
    if (seen.has(el)) continue;
    seen.add(el);
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') continue;
    if (r.bottom < 0 || r.top > (window.innerHeight + 2000)) continue; // skip far off-screen
    el.setAttribute('data-pilot-ref', String(ref));
    let name = (el.getAttribute('aria-label') || el.getAttribute('placeholder') ||
                el.getAttribute('name') || el.getAttribute('title') ||
                (el.innerText || el.value || '').trim()).replace(/\s+/g, ' ').slice(0, 120);
    items.push({
      ref: ref,
      tag: el.tagName.toLowerCase(),
      type: el.getAttribute('type') || '',
      role: el.getAttribute('role') || '',
      name: name,
      editable: ['input', 'textarea', 'select'].includes(el.tagName.toLowerCase()) ||
                el.isContentEditable
    });
    ref++;
  }
  return { url: location.href, title: document.title, items: items };
}
"""


class BrowserController:
    def __init__(self, browser_cfg, config) -> None:
        self.cfg = browser_cfg
        self.config = config
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    # --- lifecycle ---------------------------------------------------------
    def _ensure(self) -> None:
        if self._page is not None:
            return
        from playwright.sync_api import sync_playwright  # type: ignore

        self._pw = sync_playwright().start()
        engine = {
            "chromium": self._pw.chromium,
            "firefox": self._pw.firefox,
            "webkit": self._pw.webkit,
        }.get(self.cfg.engine, self._pw.chromium)

        launch_kwargs: Dict[str, Any] = {"headless": self.cfg.headless}
        if self.cfg.executable_path:
            launch_kwargs["executable_path"] = self.cfg.executable_path
        elif self.cfg.channel:
            launch_kwargs["channel"] = self.cfg.channel

        if self.cfg.user_data_dir:
            # Persistent context: keeps cookies, logins, profile across runs.
            self._context = engine.launch_persistent_context(
                self.cfg.user_data_dir,
                viewport={"width": self.cfg.viewport_width, "height": self.cfg.viewport_height},
                **launch_kwargs,
            )
            self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        else:
            self._browser = engine.launch(**launch_kwargs)
            self._context = self._browser.new_context(
                viewport={"width": self.cfg.viewport_width, "height": self.cfg.viewport_height}
            )
            self._page = self._context.new_page()
        self._context.set_default_timeout(self.cfg.nav_timeout_ms)

    @property
    def page(self):
        self._ensure()
        return self._page

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = self._browser = self._context = self._page = None

    # --- navigation --------------------------------------------------------
    def goto(self, url: str) -> str:
        if not url.startswith(("http://", "https://", "file://", "about:")):
            url = "https://" + url
        self.page.goto(url, wait_until="domcontentloaded")
        return f"Navigated to {self.page.url}"

    def back(self) -> str:
        self.page.go_back(wait_until="domcontentloaded")
        return f"Back -> {self.page.url}"

    def forward(self) -> str:
        self.page.go_forward(wait_until="domcontentloaded")
        return f"Forward -> {self.page.url}"

    def reload(self) -> str:
        self.page.reload(wait_until="domcontentloaded")
        return f"Reloaded {self.page.url}"

    def current_url(self) -> str:
        return self.page.url

    # --- perception --------------------------------------------------------
    def snapshot(self, max_items: int = 120) -> str:
        data = self.page.evaluate(_SNAPSHOT_JS)
        lines = [f"URL: {data['url']}", f"Title: {data['title']}", "Interactive elements:"]
        for it in data["items"][:max_items]:
            tag = it["type"] and f"{it['tag']}[{it['type']}]" or it["tag"]
            role = f" role={it['role']}" if it["role"] else ""
            edit = " (editable)" if it["editable"] else ""
            name = it["name"] or "(no label)"
            lines.append(f"  [{it['ref']}] {tag}{role}{edit}: {name}")
        if len(data["items"]) > max_items:
            lines.append(f"  ... {len(data['items']) - max_items} more (scroll or refine)")
        return "\n".join(lines)

    def get_text(self, max_chars: int = 6000) -> str:
        text = self.page.evaluate("() => document.body ? document.body.innerText : ''")
        text = " ".join(text.split())
        return text[:max_chars]

    def screenshot_png(self) -> bytes:
        return self.page.screenshot(type="png", full_page=False)

    # --- actions -----------------------------------------------------------
    def _locator(self, ref: int):
        return self.page.locator(f"[data-pilot-ref='{ref}']").first

    def click_ref(self, ref: int) -> str:
        self._locator(ref).click()
        self.page.wait_for_load_state("domcontentloaded")
        return f"Clicked element [{ref}]. Now at {self.page.url}"

    def type_ref(self, ref: int, text: str, submit: bool = False) -> str:
        loc = self._locator(ref)
        loc.click()
        loc.fill("")
        loc.type(text, delay=5)
        if submit:
            loc.press("Enter")
            self.page.wait_for_load_state("domcontentloaded")
        return f"Typed into [{ref}]{' and submitted' if submit else ''}."

    def fill_secret_ref(self, ref: int, value: str, submit: bool = False) -> str:
        """Fill an element with a secret value that is never exposed to the model."""
        loc = self._locator(ref)
        loc.click()
        loc.fill("")
        loc.type(value, delay=5)
        if submit:
            loc.press("Enter")
            self.page.wait_for_load_state("domcontentloaded")
        return f"Filled [{ref}] with a secret ({len(value)} chars){' and submitted' if submit else ''}."

    def press(self, key: str) -> str:
        self.page.keyboard.press(key)
        return f"Pressed {key}."

    def scroll(self, dy: int = 600) -> str:
        self.page.mouse.wheel(0, dy)
        return f"Scrolled by {dy}px."

    def wait_for(self, text: str, timeout_ms: int = 10_000) -> str:
        self.page.get_by_text(text).first.wait_for(timeout=timeout_ms)
        return f"Found text: {text!r}"

    # --- tabs --------------------------------------------------------------
    def new_tab(self, url: Optional[str] = None) -> str:
        self._ensure()
        self._page = self._context.new_page()
        if url:
            return self.goto(url)
        return "Opened a new tab."

    def list_tabs(self) -> str:
        pages = self._context.pages if self._context else []
        return "\n".join(f"  [{i}] {p.url}" for i, p in enumerate(pages)) or "(no tabs)"

    def switch_tab(self, index: int) -> str:
        pages = self._context.pages
        if 0 <= index < len(pages):
            self._page = pages[index]
            self._page.bring_to_front()
            return f"Switched to tab [{index}]: {self._page.url}"
        return f"No tab with index {index}."
