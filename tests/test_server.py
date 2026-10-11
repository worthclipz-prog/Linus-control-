"""The web control panel, driven over a real socket with a fake agent."""
import json
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from pilot.config import Config
from pilot.server import _make_handler


class FakeRunner:
    """Stands in for AgentRunner so the server test needs no model or browser."""

    def __init__(self):
        self.tasks = []
        self.answers = []
        self.stopped = False
        self._pending = None

    def events_since(self, since):
        return [{"id": 1, "kind": "task", "text": "demo"}] if since < 1 else []

    def status(self):
        return {"running": False, "current_task": None, "queued": [], "latest_event_id": 1,
                "pending": self._pending, "model": "gemini-2.0-flash", "keys": 2}

    def submit(self, task):
        self.tasks.append(task)

    def stop(self):
        self.stopped = True

    def answer(self, pid, value):
        self.answers.append((pid, value))
        return True


@pytest.fixture
def server():
    runner = FakeRunner()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(runner, "secret-token"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", runner
    httpd.shutdown()


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read().decode()


def post(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def test_requires_token(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/")
    assert e.value.code == 401


def test_wrong_token_rejected(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/?token=nope")
    assert e.value.code == 401


def test_dashboard_served_with_token(server):
    base, _ = server
    status, body = get(base + "/?token=secret-token")
    assert status == 200 and "<title>Pilot</title>" in body
    assert "__TOKEN__" not in body  # token placeholder was filled in


def test_manifest_and_assets(server):
    base, _ = server
    status, body = get(base + "/manifest.webmanifest?token=secret-token")
    assert status == 200 and json.loads(body)["name"] == "Pilot"
    assert get(base + "/sw.js?token=secret-token")[0] == 200
    assert get(base + "/icon.svg?token=secret-token")[0] == 200


def test_state_endpoint(server):
    base, _ = server
    status, body = get(base + "/api/state?since=0&token=secret-token")
    data = json.loads(body)
    assert status == 200
    assert data["events"][0]["text"] == "demo"
    assert data["status"]["keys"] == 2


def test_submit_task_and_stop(server):
    base, runner = server
    assert post(base + "/api/task?token=secret-token", {"task": "open youtube"}) == (200, {"ok": True})
    assert runner.tasks == ["open youtube"]
    post(base + "/api/stop?token=secret-token", {})
    assert runner.stopped is True


def test_empty_task_rejected(server):
    base, _ = server
    with pytest.raises(urllib.error.HTTPError) as e:
        post(base + "/api/task?token=secret-token", {"task": "  "})
    assert e.value.code == 400


def test_answer_routed_to_runner(server):
    base, runner = server
    post(base + "/api/answer?token=secret-token", {"id": 7, "value": "123456"})
    assert runner.answers == [(7, "123456")]


def test_runner_prompt_roundtrip():
    """AgentRunner parks on a confirm until answered (no network)."""
    from pilot.runner import AgentRunner

    runner = AgentRunner.__new__(AgentRunner)  # bypass __init__ (no Agent/model)
    import threading as _t
    runner._lock = _t.Lock()
    runner._events = []
    runner._seq = 0
    runner._pending = None
    runner._pending_seq = 0
    runner.max_events = 2000
    runner.config = Config(auto_approve=False)

    result = {}
    t = threading.Thread(target=lambda: result.__setitem__("ok", runner._confirm("shell", {"command": "ls"})))
    t.start()
    for _ in range(100):
        if runner.pending():
            break
        time.sleep(0.01)
    p = runner.pending()
    assert p and p["kind"] == "confirm"
    assert runner.answer(p["id"], True) is True
    t.join(timeout=2)
    assert result["ok"] is True
