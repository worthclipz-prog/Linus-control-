"""Thin wrapper around an OpenAI-compatible Chat Completions endpoint.

Works with the official OpenAI API and with any server that speaks the same
protocol: Google Gemini (its OpenAI-compatible endpoint), Azure OpenAI,
OpenRouter, Ollama, LM Studio, vLLM, ... The brain is pluggable: set
``base_url`` + ``model`` (or ``provider``) in config.

Give it several API keys and it fails over to the next one when the active
key is rate-limited or out of quota -- useful for stacking several free
Gemini keys.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


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
        api_keys,
        base_url: str,
        model: str,
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
        if isinstance(api_keys, str):
            api_keys = [api_keys]
        # A key is required by the SDK even for many local servers; use a
        # harmless placeholder when none is configured (local models).
        self.api_keys: List[str] = [k for k in (api_keys or []) if k] or ["sk-no-key-required"]
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.on_notice = on_notice or (lambda msg: None)
        self._idx = 0
        self._clients: Dict[str, Any] = {}

    def _client(self, key: str):
        if key not in self._clients:
            self._clients[key] = self._OpenAI(api_key=key, base_url=self.base_url)
        return self._clients[key]

    @property
    def active_key_index(self) -> int:
        return self._idx

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        model: Optional[str] = None,
    ) -> Any:
        kwargs: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": self.temperature,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        n = len(self.api_keys)
        last_exc: Optional[Exception] = None
        for attempt in range(n):
            key = self.api_keys[self._idx]
            try:
                resp = self._client(key).chat.completions.create(**kwargs)
                return resp.choices[0].message
            except Exception as exc:
                last_exc = exc
                if _is_rate_limit(exc) and n > 1:
                    self._idx = (self._idx + 1) % n
                    self.on_notice(
                        f"API key #{(self._idx) or n} was rate-limited; "
                        f"switching to key #{self._idx + 1} of {n}."
                    )
                    continue
                raise
        # Every key was rate-limited.
        self.on_notice(f"All {n} API keys are rate-limited or out of quota.")
        raise last_exc  # type: ignore[misc]
