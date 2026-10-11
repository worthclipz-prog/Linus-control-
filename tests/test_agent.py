"""Tests that run without an API key: a scripted fake model drives the agent."""
import json
from types import SimpleNamespace

import pytest

from pilot.agent import Agent
from pilot.config import Config, load_config
from pilot.tools import ToolResult


def _call(i, tool, **args):
    return SimpleNamespace(id=f"call_{i}", function=SimpleNamespace(name=tool, arguments=json.dumps(args)))


class FakeLLM:
    """Replays a fixed list of responses and records what it was sent."""

    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def chat(self, messages, tools=None, tool_choice="auto", model=None):
        self.sent.append(json.loads(json.dumps(messages)))
        content, calls = self.script.pop(0)
        return SimpleNamespace(content=content, tool_calls=calls)


def make_agent(script, **cfg):
    config = Config(**cfg)
    llm = FakeLLM(script)
    return Agent(config, llm=llm), llm


def test_runs_tool_then_finishes(tmp_path):
    target = tmp_path / "note.txt"
    agent, llm = make_agent(
        [
            (None, [_call(1, "file_write", path=str(target), content="hello")]),
            ("Wrote the note.", None),
        ],
        auto_approve=True,
    )
    assert agent.run("write a note") == "Wrote the note."
    assert target.read_text() == "hello"
    tool_msgs = [m for m in llm.sent[-1] if m["role"] == "tool"]
    assert "Wrote 5 chars" in tool_msgs[0]["content"]


def test_confirmation_can_block(tmp_path):
    target = tmp_path / "blocked.txt"
    asked = []
    agent, _ = make_agent(
        [
            (None, [_call(1, "file_write", path=str(target), content="x")]),
            ("ok", None),
        ],
    )
    agent.confirm = lambda name, args: asked.append(name) or False
    agent.run("try to write")
    assert asked == ["file_write"]
    assert not target.exists()


def test_tool_errors_are_reported_not_raised():
    agent, llm = make_agent([(None, [_call(1, "no_such_tool")]), ("done", None)])
    assert agent.run("x") == "done"
    assert "Unknown tool" in [m for m in llm.sent[-1] if m["role"] == "tool"][0]["content"]


def test_secret_value_never_reaches_the_model():
    typed = []
    agent, llm = make_agent(
        [(None, [_call(1, "keyboard_type_secret", name="gmail", field="password")]), ("in", None)],
        secrets={"gmail": {"username": "me@example.com", "password": "hunter2-very-secret"}},
    )
    agent.ctx._desktop = SimpleNamespace(type_secret=lambda v: typed.append(v) or f"Typed a secret value ({len(v)} characters).")
    agent.run("log in")
    assert typed == ["hunter2-very-secret"]
    assert "hunter2-very-secret" not in json.dumps(llm.sent)


def test_old_screenshots_are_dropped_from_requests(monkeypatch):
    shots = iter([b"png-one", b"png-two"])
    agent, llm = make_agent(
        [
            (None, [_call(1, "screen_screenshot")]),
            (None, [_call(2, "screen_screenshot")]),
            ("seen", None),
        ],
    )
    agent.tools["screen_screenshot"].handler = lambda: ToolResult("shot", next(shots))
    agent.run("look twice")
    last = json.dumps(llm.sent[-1])
    assert last.count("data:image/png;base64,") == 1
    assert "(older screenshot omitted)" in last


def test_interrupted_history_is_repaired():
    agent, llm = make_agent([("ok", None)])
    agent.messages.append({
        "role": "assistant", "content": "",
        "tool_calls": [{"id": "dangling", "type": "function", "function": {"name": "wait", "arguments": "{}"}}],
    })
    agent.run("next task")
    sent = llm.sent[-1]
    idx = next(i for i, m in enumerate(sent) if m.get("tool_calls"))
    assert sent[idx + 1] == {"role": "tool", "tool_call_id": "dangling", "content": "Interrupted."}


def test_step_limit():
    agent, _ = make_agent([(None, [_call(i, "list_secrets")]) for i in range(3)], max_steps=3)
    assert "step limit" in agent.run("loop forever")


def test_stop_request():
    agent, _ = make_agent([(None, [_call(1, "list_secrets")])])
    agent.on_event = lambda kind, text: setattr(agent, "stop_requested", True)
    assert agent.run("x") == "Stopped by user."


def test_all_tool_schemas_are_valid():
    agent, _ = make_agent([])
    names = [s["function"]["name"] for s in agent.schemas]
    assert len(names) == len(set(names))
    for s in agent.schemas:
        params = s["function"]["parameters"]
        assert params["type"] == "object"
        assert set(params["required"]) <= set(params["properties"])


def test_config_file_and_env(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(
        '[model]\nname = "my-model"\n[browser]\nuser_data_dir = "prof"\n'
        '[secrets.site]\nusername = "u"\npassword = "p"\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PILOT_SECRET_BANK_PASS", "envpass")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = load_config()
    assert cfg.model == "my-model"
    assert cfg.api_key == "sk-test"
    assert cfg.secret("site") == {"username": "u", "password": "p"}
    assert cfg.secret("bank") == {"password": "envpass"}
    assert cfg.browser.user_data_dir == str((tmp_path / "prof").resolve())
    assert "envpass" not in json.dumps(cfg.redacted())
    with pytest.raises(KeyError):
        cfg.secret("missing")


def test_mouse_corner_failsafe_stops_the_task():
    class FailSafeException(Exception):
        pass

    def boom(**_):
        raise FailSafeException("corner")

    agent, _ = make_agent([(None, [_call(1, "mouse_click", x=1, y=1)]), ("should not get here", None)])
    agent.tools["mouse_click"].handler = boom
    assert agent.run("click") == "Stopped by user."


def test_cli_always_answer_stops_prompting(monkeypatch):
    from pilot import cli

    answers = iter(["a"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))
    monkeypatch.setattr(cli._confirm, "always", False, raising=False)
    assert cli._confirm("shell", {"command": "ls"}) is True
    assert cli._confirm("shell", {"command": "pwd"}) is True  # no second prompt (iterator is empty)
