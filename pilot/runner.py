"""A background worker around the agent, shared by the web server.

It keeps a bounded log of events (what the agent says, each tool call and its
result), runs queued tasks one at a time on a single thread (Playwright's sync
API must stay on one thread), and routes confirmations and the agent's
questions to whoever is driving -- the terminal or the phone dashboard -- by
parking the worker on a pending prompt until an answer comes back.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .config import Config


@dataclass
class Prompt:
    id: int
    kind: str          # "confirm" | "ask"
    text: str
    _event: threading.Event = field(default_factory=threading.Event)
    answer: Any = None

    def resolve(self, answer: Any) -> None:
        self.answer = answer
        self._event.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        return self._event.wait(timeout)


class AgentRunner:
    def __init__(self, config: Config, max_events: int = 2000) -> None:
        from .agent import Agent  # local import to avoid a cycle

        self.config = config
        self.max_events = max_events
        self._lock = threading.Lock()
        self._events: List[Dict[str, Any]] = []
        self._seq = 0
        self._pending: Optional[Prompt] = None
        self._pending_seq = 0
        self._current_task: Optional[str] = None
        self._busy = threading.Event()
        self._queue: "list[str]" = []
        self._queue_cv = threading.Condition()
        self._closed = False

        self.agent = Agent(
            config,
            confirm=self._confirm,
            ask_user=self._ask,
            on_event=self._record,
        )
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # --- event log -----------------------------------------------------
    def _record(self, kind: str, text: str) -> None:
        with self._lock:
            self._seq += 1
            self._events.append({"id": self._seq, "kind": kind, "text": text, "ts": time.time()})
            if len(self._events) > self.max_events:
                self._events = self._events[-self.max_events:]

    def events_since(self, since: int) -> List[Dict[str, Any]]:
        with self._lock:
            return [e for e in self._events if e["id"] > since]

    # --- prompts (confirm / ask) --------------------------------------
    def _new_prompt(self, kind: str, text: str) -> Prompt:
        with self._lock:
            self._pending_seq += 1
            prompt = Prompt(id=self._pending_seq, kind=kind, text=text)
            self._pending = prompt
        self._record(kind, text)
        return prompt

    def _confirm(self, name: str, args: Dict[str, Any]) -> bool:
        if self.config.auto_approve:
            return True
        prompt = self._new_prompt("confirm", f"Allow {name}? {args}")
        prompt.wait()
        with self._lock:
            self._pending = None
        return bool(prompt.answer)

    def _ask(self, question: str) -> str:
        prompt = self._new_prompt("ask", question)
        prompt.wait()
        with self._lock:
            self._pending = None
        return str(prompt.answer or "")

    def pending(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._pending is None:
                return None
            return {"id": self._pending.id, "kind": self._pending.kind, "text": self._pending.text}

    def answer(self, prompt_id: int, value: Any) -> bool:
        with self._lock:
            prompt = self._pending
        if prompt is None or prompt.id != prompt_id:
            return False
        prompt.resolve(value)
        return True

    # --- task queue ----------------------------------------------------
    def submit(self, task: str) -> None:
        with self._queue_cv:
            self._queue.append(task)
            self._queue_cv.notify()

    def stop(self) -> None:
        """Ask the agent to stop the current task after the current action."""
        self.agent.stop_requested = True
        self._record("info", "Stop requested.")

    def status(self) -> Dict[str, Any]:
        with self._lock:
            latest = self._events[-1]["id"] if self._events else 0
        with self._queue_cv:
            queued = list(self._queue)
        return {
            "running": self._busy.is_set(),
            "current_task": self._current_task,
            "queued": queued,
            "latest_event_id": latest,
            "pending": self.pending(),
            "model": self.config.model,
            "keys": len(self.config.api_keys),
        }

    def _loop(self) -> None:
        while not self._closed:
            with self._queue_cv:
                while not self._queue and not self._closed:
                    self._queue_cv.wait()
                if self._closed:
                    return
                task = self._queue.pop(0)
            self._busy.set()
            self._current_task = task
            self.agent.stop_requested = False
            self._record("task", task)
            try:
                result = self.agent.run(task)
                self._record("say", result)
            except Exception as exc:
                self._record("info", f"error: {type(exc).__name__}: {exc}")
            finally:
                self._current_task = None
                self._busy.clear()

    def close(self) -> None:
        self._closed = True
        with self._queue_cv:
            self._queue_cv.notify_all()
        try:
            self.agent.close()
        except Exception:
            pass
