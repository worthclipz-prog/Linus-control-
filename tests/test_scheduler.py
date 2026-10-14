"""Scheduler: next-run math, firing, persistence, and the HTTP endpoints."""
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

import pytest

from pilot.scheduler import Scheduler, compute_next_run, describe, _tz, IST


def at(y, mo, d, h, mi, tz=IST):
    return datetime(y, mo, d, h, mi, tzinfo=_tz(tz))


def test_ist_is_plus_530():
    off = _tz("Asia/Kolkata").utcoffset(datetime(2026, 1, 1))
    assert off == timedelta(hours=5, minutes=30)


def test_once_in_future_and_past():
    job = {"every": "once", "time": "18:30", "date": "2026-10-20", "tz": IST}
    assert compute_next_run(job, at(2026, 10, 19, 10, 0)) == at(2026, 10, 20, 18, 30)
    assert compute_next_run(job, at(2026, 10, 20, 19, 0)) is None  # already passed


def test_daily_rolls_to_next_day():
    job = {"every": "daily", "time": "09:00", "tz": IST}
    assert compute_next_run(job, at(2026, 10, 14, 8, 0)) == at(2026, 10, 14, 9, 0)
    assert compute_next_run(job, at(2026, 10, 14, 9, 0)) == at(2026, 10, 15, 9, 0)  # strictly after


def test_weekly_picks_right_weekday():
    job = {"every": "weekly", "time": "07:30", "weekday": "wed", "tz": IST}
    # 2026-10-14 is a Wednesday
    assert compute_next_run(job, at(2026, 10, 14, 6, 0)) == at(2026, 10, 14, 7, 30)
    # after Wednesday's time -> next Wednesday
    assert compute_next_run(job, at(2026, 10, 14, 8, 0)) == at(2026, 10, 21, 7, 30)


def test_describe():
    assert describe({"every": "daily", "time": "09:00", "tz": IST}) == "Every day at 09:00 IST"
    assert "Wed" in describe({"every": "weekly", "time": "07:30", "weekday": "wed", "tz": IST})


@pytest.fixture
def sched(tmp_path):
    fired = []
    s = Scheduler(submit=lambda task, images=None: fired.append((task, images)),
                  path=str(tmp_path / "schedule.json"), interval=9999)
    s.close()  # we drive due_and_fire() manually; no background ticking
    s.fired = fired
    yield s


def test_add_validates(sched):
    with pytest.raises(ValueError):
        sched.add("", every="once", time_str="09:00", date="2026-10-20")
    with pytest.raises(ValueError):
        sched.add("x", every="once", time_str="09:00", date="2020-01-01")  # in the past
    with pytest.raises(ValueError):
        sched.add("x", every="daily")  # no time


def test_one_shot_fires_once_then_disables(sched):
    # schedule for "now-ish" by using a daily then forcing due
    job = sched.add("do the thing", every="once", time_str="09:00", date="2099-01-01")
    job["next_run"] = datetime.now(timezone.utc).isoformat()  # force due
    n = sched.due_and_fire(datetime.now(timezone.utc) + timedelta(seconds=1))
    assert n == 1
    assert sched.fired == [("do the thing", [])]
    assert sched.jobs[0]["enabled"] is False
    assert sched.jobs[0]["next_run"] is None
    # does not fire again
    assert sched.due_and_fire(datetime.now(timezone.utc) + timedelta(hours=1)) == 0


def test_daily_reschedules_after_firing(sched):
    job = sched.add("morning brief", every="daily", time_str="09:00")
    job["next_run"] = datetime.now(timezone.utc).isoformat()
    sched.due_and_fire(datetime.now(timezone.utc) + timedelta(seconds=1))
    assert sched.fired == [("morning brief", [])]
    assert sched.jobs[0]["enabled"] is True
    assert sched.jobs[0]["next_run"] is not None  # rescheduled for tomorrow


def test_toggle_and_remove(sched):
    job = sched.add("x", every="daily", time_str="10:00")
    assert sched.toggle(job["id"], False) and sched.jobs[0]["enabled"] is False
    # a paused job never fires even when due
    sched.jobs[0]["next_run"] = datetime.now(timezone.utc).isoformat()
    sched.jobs[0]["enabled"] = False
    assert sched.due_and_fire(datetime.now(timezone.utc) + timedelta(hours=1)) == 0
    assert sched.remove(job["id"]) and sched.jobs == []


def test_persistence_round_trip(tmp_path):
    p = tmp_path / "schedule.json"
    s1 = Scheduler(submit=lambda *a, **k: None, path=str(p), interval=9999); s1.close()
    s1.add("remember me", every="daily", time_str="08:00")
    s2 = Scheduler(submit=lambda *a, **k: None, path=str(p), interval=9999); s2.close()
    assert len(s2.jobs) == 1 and s2.jobs[0]["task"] == "remember me"


# --- HTTP endpoints ---
from pilot.server import _make_handler  # noqa: E402


class FakeRunner:
    def events_since(self, since): return []
    def status(self): return {"running": False, "current_task": None, "queued": [],
                              "latest_event_id": 0, "pending": None, "model": "m", "keys": 1}
    def submit(self, *a, **k): pass
    def stop(self): pass
    def answer(self, *a): return True


@pytest.fixture
def server(tmp_path):
    s = Scheduler(submit=lambda *a, **k: None, path=str(tmp_path / "s.json"), interval=9999); s.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(FakeRunner(), "tok", s))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    time.sleep(0.05)
    yield f"http://127.0.0.1:{httpd.server_address[1]}", s
    httpd.shutdown()


def _post(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read())


def test_endpoints_add_list_delete(server):
    base, s = server
    code, body = _post(base + "/api/schedule?token=tok",
                       {"task": "open gmail", "every": "daily", "time": "09:00"})
    assert code == 200 and body["ok"]
    jobs = _get(base + "/api/schedule?token=tok")["jobs"]
    assert len(jobs) == 1 and jobs[0]["task"] == "open gmail" and "desc" in jobs[0]
    jid = jobs[0]["id"]
    assert _post(base + "/api/schedule/toggle?token=tok", {"id": jid, "enabled": False}) == (200, {"ok": True})
    assert s.jobs[0]["enabled"] is False
    assert _post(base + "/api/schedule/delete?token=tok", {"id": jid}) == (200, {"ok": True})
    assert s.jobs == []


def test_endpoint_rejects_bad_input(server):
    base, _ = server
    code, body = _post(base + "/api/schedule?token=tok",
                       {"task": "x", "every": "once", "time": "09:00", "date": "2000-01-01"})
    assert code == 400 and "past" in body["error"].lower()


def test_schedule_requires_token(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        _get(base + "/api/schedule")
    assert e.value.code == 401
