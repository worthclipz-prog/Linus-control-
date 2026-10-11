"""Multi-key failover: rotate to the next key on a rate-limit error."""
from types import SimpleNamespace

import pytest

from pilot.config import Config, load_config
from pilot.llm import LLM, _is_rate_limit


class RateLimited(Exception):
    status_code = 429


class AuthError(Exception):
    status_code = 401


def make_llm(monkeypatch, behaviours):
    """behaviours: key -> callable(**kwargs) -> message, or raises."""
    notices = []

    class FakeCompletions:
        def __init__(self, key):
            self.key = key

        def create(self, **kwargs):
            return behaviours[self.key](**kwargs)

    class FakeClient:
        def __init__(self, api_key, base_url):
            self.chat = SimpleNamespace(completions=FakeCompletions(api_key))

    from pilot.config import Backend
    bes = [Backend("http://x/v1", "m", k) for k in ("k1", "k2", "k3")]
    llm = LLM(bes, on_notice=notices.append)
    monkeypatch.setattr(llm, "_OpenAI", FakeClient)
    return llm, notices


def test_rotates_past_exhausted_keys(monkeypatch):
    calls = []

    def limited(name):
        def f(**kwargs):
            calls.append(name)
            raise RateLimited()
        return f

    def ok(name):
        def f(**kwargs):
            calls.append(name)
            return SimpleNamespace(choices=[SimpleNamespace(message="ok")])
        return f

    behaviours = {"k1": limited("k1"), "k2": limited("k2"), "k3": ok("k3")}
    llm, notices = make_llm(monkeypatch, behaviours)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "ok"
    assert calls == ["k1", "k2", "k3"]
    assert llm.active_key_index == 2
    assert len(notices) == 2  # two rotations announced


def test_sticks_with_working_key(monkeypatch):
    def ok(**kw):
        return SimpleNamespace(choices=[SimpleNamespace(message="ok")])

    behaviours = {k: ok for k in ("k1", "k2", "k3")}
    llm, _ = make_llm(monkeypatch, behaviours)
    llm.chat([]); llm.chat([])
    assert llm.active_key_index == 0  # never rotated


def test_non_rate_limit_errors_do_not_rotate(monkeypatch):
    def boom(**kw):
        raise AuthError()

    behaviours = {k: boom for k in ("k1", "k2", "k3")}
    llm, _ = make_llm(monkeypatch, behaviours)
    with pytest.raises(AuthError):
        llm.chat([])
    assert llm.active_key_index == 0  # bailed on the first key


def test_all_keys_exhausted_raises(monkeypatch):
    def boom(**kw):
        raise RateLimited()

    behaviours = {k: boom for k in ("k1", "k2", "k3")}
    llm, notices = make_llm(monkeypatch, behaviours)
    with pytest.raises(RateLimited):
        llm.chat([])
    assert "All 3 model backend" in notices[-1]


def test_rate_limit_detection():
    assert _is_rate_limit(RateLimited())
    assert _is_rate_limit(Exception("429 RESOURCE_EXHAUSTED: quota exceeded"))
    assert _is_rate_limit(Exception("model is overloaded, try again"))
    assert not _is_rate_limit(AuthError())
    assert not _is_rate_limit(Exception("bad request"))


def test_gemini_provider_sets_base_url(tmp_path, monkeypatch):
    (tmp_path / "config.toml").write_text(
        '[model]\nprovider = "gemini"\nname = "gemini-2.0-flash"\napi_keys = ["g1", "g2"]\n'
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PILOT_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("PILOT_API_KEYS", raising=False)
    cfg = load_config()
    assert cfg.base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert cfg.api_keys == ["g1", "g2"]


def test_numbered_env_keys_add_fallbacks(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "primary")
    monkeypatch.setenv("PILOT_API_KEY_2", "second")
    monkeypatch.setenv("PILOT_API_KEY_3", "third")
    cfg = load_config()
    assert cfg.api_keys == ["primary", "second", "third"]
    assert "primary" not in str(cfg.redacted())  # redaction hides every key


def test_fails_over_to_a_different_provider(monkeypatch):
    """Primary (gemini) exhausted -> fall over to a different endpoint+model (nvidia)."""
    from pilot.config import Backend

    used = []

    class FakeCompletions:
        def __init__(self, api_key, base_url):
            self.api_key, self.base_url = api_key, base_url

        def create(self, **kwargs):
            used.append((self.base_url, kwargs["model"]))
            if "google" in self.base_url:
                raise RateLimited()
            return SimpleNamespace(choices=[SimpleNamespace(message="hi from nvidia")])

    class FakeClient:
        def __init__(self, api_key, base_url):
            self.chat = SimpleNamespace(completions=FakeCompletions(api_key, base_url))

    backends = [
        Backend("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-2.0-flash", "g1", label="gemini"),
        Backend("https://integrate.api.nvidia.com/v1", "nvidia/nemotron", "nv1", label="nvidia"),
    ]
    notices = []
    llm = LLM(backends, on_notice=notices.append)
    monkeypatch.setattr(llm, "_OpenAI", FakeClient)
    assert llm.chat([{"role": "user", "content": "hi"}]) == "hi from nvidia"
    assert used[0][1] == "gemini-2.0-flash" and used[1][1] == "nvidia/nemotron"
    assert llm.active_model == "nvidia/nemotron"
    assert "nvidia" in notices[-1]


def test_config_parses_fallback_table(tmp_path, monkeypatch):
    from pilot.config import load_config

    (tmp_path / "config.toml").write_text(
        '[model]\n'
        'provider = "gemini"\n'
        'name = "gemini-flash-lite-latest"\n'
        'api_key = "g-primary"\n\n'
        '[[model.fallbacks]]\n'
        'provider = "nvidia"\n'
        'name = "nvidia/nemotron-3.5-lightning-30b-a3b"\n'
        'api_key = "nvapi-secret"\n'
    )
    monkeypatch.chdir(tmp_path)
    for var in ("PILOT_BASE_URL", "OPENAI_API_KEY", "GEMINI_API_KEY", "PILOT_API_KEYS",
                "PILOT_FALLBACK_BASE_URL", "PILOT_FALLBACK_MODEL"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    backends = cfg.backends()
    assert [b.label for b in backends] == ["gemini", "nvidia"]
    assert backends[1].base_url == "https://integrate.api.nvidia.com/v1"
    assert backends[1].model == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert "nvapi-secret" not in str(cfg.redacted())  # fallback key redacted


def test_env_fallback_provider(monkeypatch, tmp_path):
    from pilot.config import load_config

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "g1")
    monkeypatch.setenv("PILOT_FALLBACK_PROVIDER", "nvidia")
    monkeypatch.setenv("PILOT_FALLBACK_MODEL", "nvidia/nemotron")
    monkeypatch.setenv("PILOT_FALLBACK_API_KEY", "nvapi-xyz")
    cfg = load_config()
    assert len(cfg.fallbacks) == 1
    assert cfg.fallbacks[0].base_url == "https://integrate.api.nvidia.com/v1"
    assert cfg.backends()[-1].model == "nvidia/nemotron"
