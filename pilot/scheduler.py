"""Scheduled tasks: run a task at a set time without anyone watching.

A job is a task (the same text you'd type) plus when to run it: once at a
date+time, every day at a time, or every week on a weekday at a time. Times
are in a timezone you choose (default Asia/Kolkata / IST).

A background thread checks once a short interval; when a job is due it hands
the task to the AgentRunner's queue -- exactly as if it had been typed -- so
it never interrupts a task already running. Jobs persist to a JSON file, so
they survive restarts.

Note: unattended runs should have auto_approve on, or a task that asks for a
shell/file confirmation will wait for an answer nobody is there to give.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

IST = "Asia/Kolkata"
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _tz(name: str):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    # Fallbacks when the system tz database is missing.
    if name in ("Asia/Kolkata", "IST"):
        return timezone(timedelta(hours=5, minutes=30))
    if name == "UTC":
        return timezone.utc
    return timezone.utc


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_hhmm(s: str) -> tuple:
    h, m = str(s).split(":")
    return int(h), int(m)


def compute_next_run(job: Dict[str, Any], after: datetime) -> Optional[datetime]:
    """When this job should next run, strictly after `after`. None if never
    (a one-shot whose moment has passed)."""
    tz = _tz(job.get("tz", IST))
    after = after.astimezone(tz)
    every = job.get("every", "once")
    hour, minute = _parse_hhmm(job.get("time", "00:00"))

    if every == "once":
        date = job.get("date")
        if not date:
            return None
        y, mo, d = (int(x) for x in str(date).split("-"))
        cand = datetime(y, mo, d, hour, minute, tzinfo=tz)
        return cand if cand > after else None

    if every == "daily":
        cand = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if cand <= after:
            cand += timedelta(days=1)
        return cand

    if every == "weekly":
        target = WEEKDAYS.index((job.get("weekday") or "mon").lower()[:3])
        cand = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        days = (target - cand.weekday()) % 7
        cand += timedelta(days=days)
        if cand <= after:
            cand += timedelta(days=7)
        return cand

    return None


def describe(job: Dict[str, Any]) -> str:
    every = job.get("every", "once")
    t = job.get("time", "")
    tz = "IST" if job.get("tz", IST) in (IST, "IST") else job.get("tz")
    if every == "once":
        return f"Once on {job.get('date','?')} at {t} {tz}"
    if every == "daily":
        return f"Every day at {t} {tz}"
    if every == "weekly":
        return f"Every {(job.get('weekday') or 'mon').title()} at {t} {tz}"
    return every


def _human(next_run_iso: Optional[str], tz_name: str) -> str:
    if not next_run_iso:
        return "-"
    try:
        dt = datetime.fromisoformat(next_run_iso).astimezone(_tz(tz_name))
        return dt.strftime("%b %d, %H:%M")
    except Exception:
        return next_run_iso


class Scheduler:
    def __init__(
        self,
        submit: Callable[..., None],
        path: str,
        default_tz: str = IST,
        on_event: Optional[Callable[[str, str], None]] = None,
        interval: int = 20,
    ) -> None:
        self.submit = submit
        self.path = Path(path)
        self.default_tz = default_tz
        self.on_event = on_event or (lambda k, t: None)
        self.interval = max(5, interval)
        self._lock = threading.Lock()
        self.jobs: List[Dict[str, Any]] = []
        self._load()
        self._closed = False
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    # --- persistence ---------------------------------------------------
    def _load(self) -> None:
        if self.path.is_file():
            try:
                self.jobs = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.jobs = []

    def _save(self) -> None:
        try:
            self.path.write_text(json.dumps(self.jobs, indent=2), encoding="utf-8")
        except Exception:
            pass

    # --- CRUD ----------------------------------------------------------
    def add(self, task: str, every: str = "once", time_str: Optional[str] = None,
            date: Optional[str] = None, weekday: Optional[str] = None,
            tz: Optional[str] = None, images: Optional[list] = None) -> Dict[str, Any]:
        task = (task or "").strip()
        if not task:
            raise ValueError("A scheduled task needs some text.")
        if every not in ("once", "daily", "weekly"):
            raise ValueError("Repeat must be once, daily or weekly.")
        if not time_str:
            raise ValueError("A time (HH:MM) is required.")
        _parse_hhmm(time_str)  # validate
        job = {
            "id": uuid.uuid4().hex[:8],
            "task": task,
            "every": every,
            "time": time_str,
            "date": date,
            "weekday": weekday,
            "tz": tz or self.default_tz,
            "enabled": True,
            "images": images or [],
            "last_run": None,
        }
        nxt = compute_next_run(job, _now())
        if every == "once" and nxt is None:
            raise ValueError("That date and time is in the past.")
        job["next_run"] = nxt.isoformat() if nxt else None
        with self._lock:
            self.jobs.append(job)
            self._save()
        return job

    def remove(self, job_id: str) -> bool:
        with self._lock:
            before = len(self.jobs)
            self.jobs = [j for j in self.jobs if j["id"] != job_id]
            self._save()
            return len(self.jobs) != before

    def toggle(self, job_id: str, enabled: bool) -> bool:
        with self._lock:
            for j in self.jobs:
                if j["id"] == job_id:
                    j["enabled"] = bool(enabled)
                    if enabled and not j.get("next_run"):
                        nxt = compute_next_run(j, _now())
                        j["next_run"] = nxt.isoformat() if nxt else None
                    self._save()
                    return True
        return False

    def list(self) -> List[Dict[str, Any]]:
        with self._lock:
            out = []
            for j in self.jobs:
                out.append({**j, "desc": describe(j),
                            "next_human": _human(j.get("next_run"), j.get("tz", self.default_tz))})
            return out

    # --- firing --------------------------------------------------------
    def due_and_fire(self, now: Optional[datetime] = None) -> int:
        now = now or _now()
        fired = 0
        with self._lock:
            for job in self.jobs:
                if not job.get("enabled") or not job.get("next_run"):
                    continue
                try:
                    due = datetime.fromisoformat(job["next_run"]) <= now
                except Exception:
                    continue
                if due:
                    self._fire_locked(job, now)
                    fired += 1
            if fired:
                self._save()
        return fired

    def _fire_locked(self, job: Dict[str, Any], now: datetime) -> None:
        self.on_event("info", f"Scheduled task firing: {job['task'][:80]}")
        try:
            self.submit(job["task"], job.get("images") or [])
        except Exception as exc:  # pragma: no cover
            self.on_event("info", f"Failed to start scheduled task: {exc}")
        job["last_run"] = now.isoformat()
        if job.get("every") == "once":
            job["enabled"] = False
            job["next_run"] = None
        else:
            nxt = compute_next_run(job, now + timedelta(seconds=1))
            job["next_run"] = nxt.isoformat() if nxt else None

    def _loop(self) -> None:
        while not self._closed:
            try:
                self.due_and_fire()
            except Exception as exc:  # pragma: no cover
                self.on_event("info", f"scheduler error: {exc}")
            for _ in range(self.interval):
                if self._closed:
                    return
                time.sleep(1)

    def close(self) -> None:
        self._closed = True
