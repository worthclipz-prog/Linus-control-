"""Thin wrapper around one or more OpenAI-compatible Chat Completions backends.

Each backend is an endpoint + model + key (see config.Backend). Pilot tries
them in order and fails over to the next when the active one is rate-limited
or out of quota. Backends can be different providers, so you can run, say,
Gemini as the primary and NVIDIA NIM as the fallback.
"""
from __future__ import annotations

from typing import Any, Callable, List, Optional

from .config import Backend


def _is_rate_limit(exc: Exception) -> bool:
    """True for 429 (rate limit / quota) and 503 (overloaded) style errors."""
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status in (429, 503):
        return True
    name = type(exc).__name__.lower()
    if "ratelimit" in name:
        return True
    text = str(exc).lower()
    return any(s in text for s in ("rate limit", "quota", "resource_exhausted", "overloaded", "too many requests"))


class LLM:
    def __init__(
        self,
        backends,
        temperature: float = 0.0,
        on_notice: Optional[Callable[[str], None]] = None,
    ):
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "The 'openai' package is required. Install deps with "
                "`pip install -r requirements.txt`."
            ) from exc
        self._OpenAI = OpenAI
        if isinstance(backends, Backend):
            backends = [backends]
        self.backends: List[Backend] = list(backends) or [Backend("https://api.openai.com/v1", "gpt-4o")]
        self.temperature = temperature
        self.on_notice = on_notice or (lambda msg: None)
        self._idx = 0
        self._clients: dict = {}

    def _client(self, b: Backend):
        key = (b.base_url, b.api_key or "sk-no-key-required")
        if key not in self._clients:
            self._clients[key] = self._OpenAI(api_key=key[1], base_url=b.base_url)
        return self._clients[key]

    @property
    def active_key_index(self) -> int:
        return self._idx

    @property
    def active_model(self) -> str:
        return self.backends[self._idx].model

    def _describe(self, i: int) -> str:
        b = self.backends[i]
        return f"{b.label or b.base_url} ({b.model})"

    def chat(
        self,
        messages: List[dict],
        tools: Optional[List[dict]] = None,
        tool_choice: str = "auto",
        prefer_vision: bool = False,
    ) -> Any:
        n = len(self.backends)
        last_exc: Optional[Exception] = None
        for _ in range(n):
            b = self.backends[self._idx]
            model = b.vision_model if (prefer_vision and b.vision_model) else b.model
            kwargs: dict = {"model": model, "messages": messages, "temperature": self.temperature}
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = tool_choice
            try:
                resp = self._client(b).chat.completions.create(**kwargs)
                return resp.choices[0].message
            except Exception as exc:
                last_exc = exc
                if _is_rate_limit(exc) and n > 1:
                    prev = self._idx
                    self._idx = (self._idx + 1) % n
                    self.on_notice(
                        f"Model {self._describe(prev)} is rate-limited; "
                        f"switching to {self._describe(self._idx)}."
                    )
                    continue
                raise
        self.on_notice(f"All {n} model backend(s) are rate-limited or out of quota.")
        raise last_exc  # type: ignore[misc]
