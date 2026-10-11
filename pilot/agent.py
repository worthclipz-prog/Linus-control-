"""The agent loop: model picks a tool, Pilot runs it, repeat until done.

Speed comes from three things:
  * Web work goes through DOM snapshots, not screenshots, so most steps are
    small text-only requests.
  * Old screenshots and long tool outputs are pruned from the history, so
    every request stays small no matter how long the task runs.
  * The system prompt tells the model to act, not deliberate.
"""
from __future__ import annotations

import base64
import json
from typing import Any, Callable, Dict, List, Optional

from .config import Config
from .llm import LLM
from .platform_utils import platform_summary
from .tools import Tool, ToolContext, ToolResult, build_tools

SYSTEM_PROMPT = """You are Pilot, an agent that operates the user's own computer ({platform}).
Act, don't deliberate. Each turn, make the single best next tool call. Keep any text to one short sentence.

Speed rules:
- For anything on the web, use the browser_* tools and act on [ref] numbers from snapshots. Never guess coordinates in the browser.
- browser_goto/click/scroll and submitting browser_type already return a fresh snapshot; don't call browser_snapshot right after them.
- Go straight to the right URL instead of clicking through menus (e.g. https://mail.google.com, https://github.com/settings).
- Use screen_screenshot with mouse_*/keyboard_* only for native desktop apps. Prefer keyboard shortcuts over clicks.
- Use shell for anything faster on the command line (opening files, moving things, installing software).

Logins: stored credentials are available by name (see list_secrets). Fill them with browser_fill_secret or keyboard_type_secret; you never see the values and must not ask for them.
If a site asks for a one-time code, a CAPTCHA, or approval on another device, call ask_user and wait for the user.

When the task is complete, reply with a one or two sentence summary and no tool call."""

# How many of the most recent messages keep their full tool output.
_KEEP_FULL = 6
_TRUNCATE_TO = 400


class Agent:
    def __init__(
        self,
        config: Config,
        llm: Optional[LLM] = None,
        confirm: Optional[Callable[[str, Dict[str, Any]], bool]] = None,
        ask_user: Optional[Callable[[str], str]] = None,
        on_event: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.config = config
        self.llm = llm or LLM(config.api_key, config.base_url, config.model, config.temperature)
        self.ctx = ToolContext(config)
        self.confirm = confirm or (lambda name, args: True)
        self.ask_user = ask_user or (lambda q: "")
        self.on_event = on_event or (lambda kind, text: None)
        self.stop_requested = False

        tools = build_tools(self.ctx)
        tools.append(
            Tool(
                "ask_user",
                "Ask the user a question and wait for their answer (2FA codes, CAPTCHAs, choices).",
                {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
                lambda question: ToolResult(self.ask_user(question) or "(no answer)"),
            )
        )
        self.tools: Dict[str, Tool] = {t.name: t for t in tools}
        self.schemas = [t.schema() for t in tools]
        self.messages: List[Dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(platform=platform_summary())}
        ]
        self._image_msg_indices: List[int] = []

    # ------------------------------------------------------------------
    def run(self, task: str) -> str:
        """Run one task to completion and return the model's final summary."""
        self.stop_requested = False
        self._repair_history()
        self.messages.append({"role": "user", "content": task})

        for step in range(1, self.config.max_steps + 1):
            if self.stop_requested:
                return "Stopped by user."

            use_vision = bool(self._image_msg_indices) and self._image_msg_indices[-1] == len(self.messages) - 1
            model = self.config.vision_model if (use_vision and self.config.vision_model) else None
            msg = self.llm.chat(self._prepared_messages(), tools=self.schemas, model=model)

            assistant: Dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            tool_calls = getattr(msg, "tool_calls", None) or []
            if tool_calls:
                assistant["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"},
                    }
                    for c in tool_calls
                ]
            self.messages.append(assistant)

            if not tool_calls:
                return msg.content or "Done."
            if msg.content:
                self.on_event("say", msg.content)

            images: List[bytes] = []
            for call in tool_calls:
                if self.stop_requested:
                    result = ToolResult("Skipped: stopped by user.")
                else:
                    result = self._execute(call.function.name, call.function.arguments)
                self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result.text})
                if result.image_png:
                    images.append(result.image_png)

            if images:
                content: List[Dict[str, Any]] = [{"type": "text", "text": "Screenshot from the last action:"}]
                for png in images:
                    url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
                    content.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})
                self.messages.append({"role": "user", "content": content})
                self._image_msg_indices.append(len(self.messages) - 1)

        return f"Stopped after reaching the step limit ({self.config.max_steps})."

    def close(self) -> None:
        self.ctx.close()

    # ------------------------------------------------------------------
    def _execute(self, name: str, raw_args: Optional[str]) -> ToolResult:
        try:
            args = json.loads(raw_args or "{}")
        except json.JSONDecodeError as exc:
            return ToolResult(f"Invalid JSON arguments for {name}: {exc}")

        tool = self.tools.get(name)
        if tool is None:
            return ToolResult(f"Unknown tool {name!r}.")

        self.on_event("tool", f"{name} {json.dumps(args, ensure_ascii=False)[:200]}")
        if tool.needs_confirm and not self.config.auto_approve:
            if not self.confirm(name, args):
                return ToolResult("The user declined this action. Try another approach or ask the user.")
        try:
            result = tool.handler(**args)
        except Exception as exc:
            if type(exc).__name__ == "FailSafeException":
                # pyautogui's emergency stop: the user slammed the mouse into a corner.
                self.stop_requested = True
                return ToolResult("Emergency stop triggered by the user.")
            result = ToolResult(f"Error: {type(exc).__name__}: {exc}")
        self.on_event("result", result.text[:300])
        return result

    def _repair_history(self) -> None:
        """Answer tool calls left pending by an interrupted task, so the API accepts the history."""
        answered = {m.get("tool_call_id") for m in self.messages if m["role"] == "tool"}
        repaired: List[Dict[str, Any]] = []
        for m in self.messages:
            repaired.append(m)
            for call in m.get("tool_calls", []) if m["role"] == "assistant" else []:
                if call["id"] not in answered:
                    repaired.append({"role": "tool", "tool_call_id": call["id"], "content": "Interrupted."})
        if len(repaired) != len(self.messages):
            self.messages = repaired
            self._image_msg_indices = [i for i, m in enumerate(self.messages)
                                       if m["role"] == "user" and isinstance(m["content"], list)]

    def _prepared_messages(self) -> List[Dict[str, Any]]:
        """History with old screenshots dropped and old tool output trimmed."""
        images = set(self._image_msg_indices)
        latest_image = self._image_msg_indices[-1] if self._image_msg_indices else -1
        cutoff = len(self.messages) - _KEEP_FULL
        out: List[Dict[str, Any]] = []
        for i, m in enumerate(self.messages):
            if i in images and i != latest_image:
                out.append({"role": "user", "content": "(older screenshot omitted)"})
            elif m["role"] == "tool" and i < cutoff and len(m["content"]) > _TRUNCATE_TO:
                out.append({**m, "content": m["content"][:_TRUNCATE_TO] + " ...(trimmed)"})
            else:
                out.append(m)
        return out
