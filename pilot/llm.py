"""Thin wrapper around an OpenAI-compatible Chat Completions endpoint.

Works with the official OpenAI API and with any server that speaks the same
protocol (Azure OpenAI, Ollama, LM Studio, vLLM, OpenRouter, ...). The brain
is pluggable: set ``base_url`` + ``model`` in config.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


class LLM:
    def __init__(self, api_key: str, base_url: str, model: str, temperature: float = 0.0):
        try:
            from openai import OpenAI
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "The 'openai' package is required. Install deps with "
                "`pip install -r requirements.txt`."
            ) from exc
        # A key is required by the SDK even for many local servers; use a
        # harmless placeholder when the user left it blank (local models).
        self._client = OpenAI(api_key=api_key or "sk-no-key-required", base_url=base_url)
        self.model = model
        self.temperature = temperature

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
        resp = self._client.chat.completions.create(**kwargs)
        return resp.choices[0].message
