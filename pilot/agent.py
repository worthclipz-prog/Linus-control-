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
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

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

Make ONE tool call per turn. When the task is complete, reply with a one or two sentence summary and no tool call.

If you cannot call a tool through the function interface, reply with ONLY a single JSON object and nothing else:
{{"tool": "<tool_name>", "arguments": {{ ... }}}}"""

# Common wrong argument names small models use, mapped to the real one.
_ARG_ALIASES: Dict[str, List[str]] = {
    "url": ["link", "address", "href", "website", "site"],
    "ref": ["index", "element", "element_id", "elementId", "idx", "ref_id", "number"],
    "text": ["content", "value", "input", "string", "query"],
    "command": ["cmd", "bash", "shell", "script"],
    "path": ["file", "filename", "filepath", "file_path"],
    "name": ["secret", "secret_name", "credential"],
    "field": ["part"],
    "question": ["prompt", "message", "msg"],
    "keys": ["key", "hotkey", "combo"],
    "amount": ["delta", "distance", "pixels"],
    "dy": ["delta", "distance", "pixels", "amount"],
    "seconds": ["duration", "secs", "time"],
    "x": ["X", "pos_x", "left"],
    "y": ["Y", "pos_y", "top"],
}


def _loads_lenient(s: Any) -> Any:
    """Parse JSON that small models often get slightly wrong (single quotes,
    trailing commas, surrounding prose)."""
    if isinstance(s, (dict, list)):
        return s
    if not isinstance(s, str):
        return {}
    s = s.strip()
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        pass
    i, j = s.find("{"), s.rfind("}")
    if i != -1 and j > i:
        sub = s[i:j + 1]
        for attempt in (sub,
                        re.sub(r",\s*([}\]])", r"\1", sub),
                        re.sub(r"'", '"', sub),
                        re.sub(r",\s*([}\]])", r"\1", re.sub(r"'", '"', sub))):
            try:
                return json.loads(attempt)
            except Exception:
                continue
    return {}


def _coerce_type(val: Any, spec: Dict[str, Any]) -> Any:
    t = spec.get("type")
    try:
        if t == "integer":
            return int(float(str(val).strip()))
        if t == "number":
            return float(str(val).strip())
        if t == "boolean":
            if isinstance(val, bool):
                return val
            return str(val).strip().lower() in ("1", "true", "yes", "on")
        if t == "array":
            if isinstance(val, list):
                return val
            parts = re.split(r"[+\s,]+", str(val).strip())
            return [p for p in parts if p]
        if t == "string" and not isinstance(val, str):
            return str(val)
    except (ValueError, TypeError):
        return val
    return val

# How many of the most recent messages keep their full tool output.
_KEEP_FULL = 6
_TRUNCATE_TO = 400
# Marks an auto-captured screenshot message (so it can be pruned; user-attached
# images carry the task text instead and are kept).
_SCREENSHOT_PREFIX = "Screenshot from the last action:"


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
        self.on_event = on_event or (lambda kind, text: None)
        self.llm = llm or LLM(
            config.backends(), config.temperature,
            on_notice=lambda msg: self.on_event("info", msg),
        )
        self.ctx = ToolContext(config)
        self.confirm = confirm or (lambda name, args: True)
        self.ask_user = ask_user or (lambda q: "")
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
        self._image_msg_indices: List[int] = []   # auto screenshots (pruned to latest)
        self._user_image_indices: set = set()      # user-attached images (kept)
        self._last_sig: Optional[str] = None       # repeat-action guard
        self._repeat = 0

    # ------------------------------------------------------------------
    def run(self, task: str, images: Optional[List[str]] = None) -> str:
        """Run one task to completion and return the model's final summary.

        `images` is an optional list of data: URLs (e.g. a photo the user
        attached from the phone); they are shown to the model with the task and
        kept for the whole task (auto-captured screenshots get pruned, these
        don't)."""
        self.stop_requested = False
        self._repair_history()
        if images:
            content: List[Dict[str, Any]] = [{"type": "text", "text": task or "(see image)"}]
            for url in images:
                content.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})
            self.messages.append({"role": "user", "content": content})
            self._user_image_indices.add(len(self.messages) - 1)
        else:
            self.messages.append({"role": "user", "content": task})

        for step in range(1, self.config.max_steps + 1):
            if self.stop_requested:
                return "Stopped by user."

            last = len(self.messages) - 1
            use_vision = (bool(self._image_msg_indices) and self._image_msg_indices[-1] == last) \
                or (last in self._user_image_indices)
            msg = self.llm.chat(self._prepared_messages(), tools=self.schemas, prefer_vision=use_vision)

            tool_calls = getattr(msg, "tool_calls", None) or []
            self.messages.append(self._assistant_message(msg))

            if tool_calls:
                if msg.content:
                    self.on_event("say", msg.content)
                images: List[bytes] = []
                for call in tool_calls:
                    if self.stop_requested:
                        result = ToolResult("Skipped: stopped by user.")
                    else:
                        result = self._execute(call.function.name, _loads_lenient(call.function.arguments))
                    self.messages.append({"role": "tool", "tool_call_id": call.id, "content": result.text})
                    if result.image_png:
                        images.append(result.image_png)
                self._attach_screenshots(images)
                continue

            # No native tool call. Many small local models can't use the function
            # API and instead write the action as JSON in the text -- parse that.
            parsed = self._parse_text_action(msg.content)
            if parsed is None:
                return msg.content or "Done."
            name, args = parsed
            if self.stop_requested:
                return "Stopped by user."
            result = self._execute(name, args)
            feedback = f"Result of {name}:\n{result.text}"
            if result.image_png:
                url = "data:image/png;base64," + base64.b64encode(result.image_png).decode("ascii")
                self.messages.append({"role": "user", "content": [
                    {"type": "text", "text": feedback}, {"type": "text", "text": _SCREENSHOT_PREFIX},
                    {"type": "image_url", "image_url": {"url": url, "detail": "high"}}]})
                self._image_msg_indices.append(len(self.messages) - 1)
            else:
                self.messages.append({"role": "user", "content": feedback})

        return f"Stopped after reaching the step limit ({self.config.max_steps})."

    def close(self) -> None:
        self.ctx.close()

    @staticmethod
    def _assistant_message(msg: Any) -> Dict[str, Any]:
        """Serialize the model's reply for the history, preserving provider-specific
        fields. Gemini attaches a `thought_signature` to each tool call and rejects
        the next request (HTTP 400) unless it is echoed back, so we round-trip the
        raw message rather than rebuilding it from id/name/arguments alone."""
        dump = getattr(msg, "model_dump", None)
        if callable(dump):
            try:
                data = dump(exclude_none=True)
                data["role"] = "assistant"
                data.setdefault("content", getattr(msg, "content", None))
                return data
            except Exception:
                pass
        # Fallback for plain objects (e.g. the test doubles).
        assistant: Dict[str, Any] = {"role": "assistant", "content": getattr(msg, "content", "") or ""}
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
        return assistant

    # ------------------------------------------------------------------
    def _attach_screenshots(self, images: List[bytes]) -> None:
        if not images:
            return
        shot: List[Dict[str, Any]] = [{"type": "text", "text": _SCREENSHOT_PREFIX}]
        for png in images:
            url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            shot.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})
        self.messages.append({"role": "user", "content": shot})
        self._image_msg_indices.append(len(self.messages) - 1)

    def _resolve_tool(self, name: str) -> Optional[str]:
        """Match a tool name forgivingly: exact, case-insensitive, then ignoring
        punctuation (so 'Browser Goto' or 'browser-goto' find 'browser_goto')."""
        if not name:
            return None
        if name in self.tools:
            return name
        low = name.strip().lower()
        for t in self.tools:
            if t.lower() == low:
                return t
        norm = re.sub(r"[^a-z0-9]", "", low)
        for t in self.tools:
            if re.sub(r"[^a-z0-9]", "", t.lower()) == norm:
                return t
        return None

    def _coerce_args(self, tool: Tool, args: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        """Map aliases to real parameter names, coerce types, drop unknown keys.
        Returns (clean_args, missing_required)."""
        props = tool.parameters.get("properties", {})
        required = tool.parameters.get("required", [])
        if not isinstance(args, dict):
            args = {}
        lowered = {str(k).lower(): k for k in args}
        out: Dict[str, Any] = {}
        for prop, spec in props.items():
            key = None
            if prop in args:
                key = prop
            elif prop.lower() in lowered:
                key = lowered[prop.lower()]
            else:
                for alias in _ARG_ALIASES.get(prop, []):
                    if alias in args:
                        key = alias
                        break
                    if alias.lower() in lowered:
                        key = lowered[alias.lower()]
                        break
            if key is not None:
                out[prop] = _coerce_type(args[key], spec)
        missing = [p for p in required if p not in out]
        return out, missing

    def _parse_text_action(self, content: Optional[str]) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Extract an action a text-only model wrote as JSON in its message."""
        if not content:
            return None
        candidates: List[str] = []
        for m in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.S):
            candidates.append(m.group(1))
        candidates.append(content)
        i, j = content.find("{"), content.rfind("}")
        if i != -1 and j > i:
            candidates.append(content[i:j + 1])
        for cand in candidates:
            obj = _loads_lenient(cand)
            if isinstance(obj, list) and obj and isinstance(obj[0], dict):
                obj = obj[0]
            if not isinstance(obj, dict):
                continue
            name = obj.get("tool") or obj.get("name") or obj.get("action") or obj.get("tool_name")
            args = obj.get("arguments") or obj.get("args") or obj.get("parameters") or obj.get("input") or {}
            fn = obj.get("function")
            if not name and isinstance(fn, dict):
                name = fn.get("name")
                args = fn.get("arguments") or args
            if isinstance(args, str):
                args = _loads_lenient(args)
            canon = self._resolve_tool(str(name)) if name else None
            if canon and isinstance(args, dict):
                return canon, args
        return None

    def _execute(self, name: str, args: Any) -> ToolResult:
        canon = self._resolve_tool(name)
        if canon is None:
            return ToolResult(
                f"Unknown tool {name!r}. Available tools: {', '.join(sorted(self.tools))}."
            )
        tool = self.tools[canon]
        clean, missing = self._coerce_args(tool, args if isinstance(args, dict) else {})

        self.on_event("tool", f"{canon} {json.dumps(clean, ensure_ascii=False)[:200]}")
        if missing:
            need = ", ".join(
                f"{m} ({tool.parameters['properties'].get(m, {}).get('type', 'value')})" for m in missing
            )
            got = list(args) if isinstance(args, dict) else args
            return ToolResult(f"{canon} needs these arguments: {need}. You sent: {got}. Call it again with them.")

        # Repeat-action guard: nudge if the exact same call keeps happening.
        sig = canon + "|" + json.dumps(clean, sort_keys=True, ensure_ascii=False)
        self._repeat = self._repeat + 1 if sig == self._last_sig else 0
        self._last_sig = sig

        if tool.needs_confirm and not self.config.auto_approve:
            if not self.confirm(canon, clean):
                return ToolResult("The user declined this action. Try another approach or ask the user.")
        try:
            result = tool.handler(**clean)
        except TypeError as exc:
            result = ToolResult(f"{canon} rejected those arguments ({exc}). Check the argument names and types.")
        except Exception as exc:
            if type(exc).__name__ == "FailSafeException":
                # pyautogui's emergency stop: the user slammed the mouse into a corner.
                self.stop_requested = True
                return ToolResult("Emergency stop triggered by the user.")
            result = ToolResult(f"Error: {type(exc).__name__}: {exc}")

        if self._repeat >= 2:
            result = ToolResult(
                result.text + f"\n(Note: you've made this same call {self._repeat + 1} times. "
                "If it isn't working, try a different approach or call ask_user.)",
                result.image_png,
            )
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
            # Re-derive image indices after the shift, keeping screenshots and
            # user-attached images apart (only screenshots get pruned later).
            self._image_msg_indices = []
            self._user_image_indices = set()
            for i, m in enumerate(self.messages):
                if m["role"] == "user" and isinstance(m["content"], list):
                    first = (m["content"][0].get("text", "") if m["content"] else "")
                    if first.startswith(_SCREENSHOT_PREFIX):
                        self._image_msg_indices.append(i)
                    else:
                        self._user_image_indices.add(i)

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
