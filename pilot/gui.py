"""Minimal desktop window (tkinter, ships with Python on Windows and most Linux).

All agent work happens on one background worker thread, because Playwright's
sync API must stay on the thread that started it.
"""
from __future__ import annotations

import queue
import threading
from typing import Any, Dict, Optional

from .config import Config


def run_gui(cfg: Config) -> int:
    import tkinter as tk
    from tkinter import scrolledtext, simpledialog, messagebox

    from .agent import Agent

    root = tk.Tk()
    root.title("Pilot")
    root.geometry("640x520")
    root.attributes("-topmost", True)

    log = scrolledtext.ScrolledText(root, wrap="word", state="disabled", font=("TkFixedFont", 10))
    log.pack(fill="both", expand=True, padx=8, pady=(8, 4))

    bar = tk.Frame(root)
    bar.pack(fill="x", padx=8, pady=(0, 8))
    entry = tk.Entry(bar, font=("TkDefaultFont", 11))
    entry.pack(side="left", fill="x", expand=True)
    run_btn = tk.Button(bar, text="Run")
    run_btn.pack(side="left", padx=4)
    stop_btn = tk.Button(bar, text="Stop", state="disabled")
    stop_btn.pack(side="left")

    ui_queue: "queue.Queue[tuple]" = queue.Queue()
    tasks: "queue.Queue[Optional[str]]" = queue.Queue()

    def append(text: str) -> None:
        log.configure(state="normal")
        log.insert("end", text + "\n")
        log.see("end")
        log.configure(state="disabled")

    # Callbacks run on the worker thread; they hand work to the UI thread and
    # block on a reply where an answer is needed.
    def on_event(kind: str, text: str) -> None:
        prefix = {"say": "pilot> ", "tool": "  -> ", "result": "     "}.get(kind, "")
        ui_queue.put(("log", prefix + text))

    def ask_ui(kind: str, payload: Any) -> Any:
        reply: "queue.Queue[Any]" = queue.Queue(maxsize=1)
        ui_queue.put((kind, payload, reply))
        return reply.get()

    def confirm(name: str, args: Dict[str, Any]) -> bool:
        return bool(ask_ui("confirm", f"Allow {name}?\n\n{args}"))

    def ask_user(question: str) -> str:
        return ask_ui("ask", question) or ""

    agent_holder: Dict[str, Agent] = {}

    def worker() -> None:
        agent = Agent(cfg, confirm=confirm, ask_user=ask_user, on_event=on_event)
        agent_holder["agent"] = agent
        try:
            while True:
                task = tasks.get()
                if task is None:
                    break
                try:
                    result = agent.run(task)
                    ui_queue.put(("log", "pilot> " + result))
                except Exception as exc:
                    ui_queue.put(("log", f"error: {type(exc).__name__}: {exc}"))
                ui_queue.put(("idle",))
        finally:
            agent.close()

    threading.Thread(target=worker, daemon=True).start()

    def submit(_event=None) -> None:
        task = entry.get().strip()
        if not task:
            return
        entry.delete(0, "end")
        append(f"\nyou> {task}")
        run_btn.configure(state="disabled")
        stop_btn.configure(state="normal")
        tasks.put(task)

    def stop() -> None:
        agent = agent_holder.get("agent")
        if agent:
            agent.stop_requested = True
            append("* stopping after the current action ...")

    def pump() -> None:
        try:
            while True:
                item = ui_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    append(item[1])
                elif kind == "idle":
                    run_btn.configure(state="normal")
                    stop_btn.configure(state="disabled")
                elif kind == "confirm":
                    item[2].put(messagebox.askyesno("Pilot", item[1], parent=root))
                elif kind == "ask":
                    item[2].put(simpledialog.askstring("Pilot asks", item[1], parent=root))
        except queue.Empty:
            pass
        root.after(80, pump)

    def on_close() -> None:
        stop()
        tasks.put(None)
        root.destroy()

    run_btn.configure(command=submit)
    stop_btn.configure(command=stop)
    entry.bind("<Return>", submit)
    root.protocol("WM_DELETE_WINDOW", on_close)
    append("Pilot is ready. Type a task and press Enter.\n"
           "Emergency stop: press Stop, or slam the mouse into a screen corner.")
    entry.focus_set()
    root.after(80, pump)
    root.mainloop()
    return 0
