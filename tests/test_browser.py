"""Drives a real headless browser against a local login page. Skipped if no browser is installed."""
import os

import pytest

from pilot.config import BrowserConfig, Config
from pilot.tools.browser import BrowserController

PAGE = """<!doctype html><title>Login</title>
<form onsubmit="event.preventDefault(); document.body.innerHTML =
  '<h1>Welcome ' + this.user.value + (this.pw.value === 's3cret' ? ' OK' : ' BAD') + '</h1>';">
  <input name="user" placeholder="Email">
  <input name="pw" type="password" placeholder="Password">
  <button>Sign in</button>
</form>
<div style="display:none"><button>Hidden</button></div>"""


@pytest.fixture
def browser(tmp_path):
    pytest.importorskip("playwright")
    page = tmp_path / "login.html"
    page.write_text(PAGE)
    # PILOT_BROWSER_PATH lets this run against any installed Chromium/Firefox binary.
    cfg = BrowserConfig(headless=True, executable_path=os.environ.get("PILOT_BROWSER_PATH"))
    b = BrowserController(cfg, Config())
    try:
        b.goto(page.as_uri())
    except Exception as exc:  # no browser binary available
        b.close()
        pytest.skip(f"browser unavailable: {exc}")
    yield b
    b.close()


def test_snapshot_lists_only_visible_elements(browser):
    snap = browser.snapshot()
    assert "Email" in snap and "Password" in snap and "Sign in" in snap
    assert "Hidden" not in snap


def test_login_flow_by_ref(browser):
    lines = browser.snapshot().splitlines()
    ref = lambda label: int(next(l for l in lines if label in l).split("[")[1].split("]")[0])
    browser.type_ref(ref("Email"), "me@example.com")
    browser.fill_secret_ref(ref("Password"), "s3cret")
    browser.click_ref(ref("Sign in"))
    assert "Welcome me@example.com OK" in browser.get_text()


def test_agent_logs_in_end_to_end(browser, tmp_path):
    """Full stack: scripted model -> agent -> tools -> real browser."""
    import json
    from types import SimpleNamespace

    from pilot.agent import Agent

    def call(i, tool, **args):
        return SimpleNamespace(id=f"c{i}", function=SimpleNamespace(name=tool, arguments=json.dumps(args)))

    script = [
        (None, [call(1, "browser_goto", url=(tmp_path / "login.html").as_uri())]),
        (None, [call(2, "browser_fill_secret", ref=0, name="site", field="username"),
                call(3, "browser_fill_secret", ref=1, name="site", field="password", submit=True)]),
        (None, [call(4, "browser_read")]),
        ("Logged in.", None),
    ]
    sent = []

    class FakeLLM:
        def chat(self, messages, tools=None, tool_choice="auto", model=None):
            sent.append(json.dumps(messages))
            content, calls = script.pop(0)
            return SimpleNamespace(content=content, tool_calls=calls)

    agent = Agent(Config(secrets={"site": {"username": "me@example.com", "password": "s3cret"}}), llm=FakeLLM())
    agent.ctx._browser = browser
    assert agent.run("log into the site") == "Logged in."
    assert "Welcome me@example.com OK" in sent[-1]
    assert "s3cret" not in "".join(sent)
