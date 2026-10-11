"""Configuration loading for Pilot.

Config is resolved from three sources, later ones overriding earlier ones:
  1. Built-in defaults (below).
  2. A TOML config file (``config.toml`` in the working dir, or the path in
     ``PILOT_CONFIG``).
  3. Environment variables (and a ``.env`` file if present).

Secrets (credentials the agent may type on your behalf) live only on your
machine -- in the ``[secrets]`` table of the config file or in ``PILOT_SECRET_*``
environment variables. They are never placed into the text the language model
sees; the model refers to them only by name.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore


def _load_dotenv() -> None:
    """Load a local .env file into os.environ without extra dependencies."""
    for candidate in (Path.cwd() / ".env", Path.home() / ".pilot.env"):
        if not candidate.is_file():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


@dataclass
class BrowserConfig:
    engine: str = "chromium"          # chromium | firefox | webkit
    channel: Optional[str] = None     # e.g. "chrome", "msedge" for a real install
    # Path to any Chromium-based (Brave, Vivaldi, Opera, ...) or Firefox binary.
    executable_path: Optional[str] = None
    headless: bool = False
    user_data_dir: Optional[str] = None  # persist cookies/logins across runs
    viewport_width: int = 1280
    viewport_height: int = 800
    nav_timeout_ms: int = 30_000


@dataclass
class Config:
    # --- model / brain ---
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    # Optional separate model for vision-only "look at the screen" steps.
    vision_model: Optional[str] = None
    temperature: float = 0.0
    max_steps: int = 40

    # --- behaviour ---
    # When False, destructive/irreversible actions (shell, file writes) ask
    # for confirmation at the terminal first.
    auto_approve: bool = False

    browser: BrowserConfig = field(default_factory=BrowserConfig)

    # name -> {"username": ..., "password": ..., "value": ...}
    secrets: Dict[str, Dict[str, str]] = field(default_factory=dict)

    config_dir: Path = field(default_factory=Path.cwd)

    def secret(self, name: str) -> Dict[str, str]:
        if name not in self.secrets:
            raise KeyError(
                f"No secret named {name!r}. Known secrets: "
                f"{sorted(self.secrets) or '(none configured)'}"
            )
        return self.secrets[name]

    def redacted(self) -> Dict[str, Any]:
        d = asdict(self)
        d["api_key"] = "***" if self.api_key else ""
        d["secrets"] = {k: {kk: "***" for kk in v} for k, v in self.secrets.items()}
        d["config_dir"] = str(self.config_dir)
        return d


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def load_config(path: Optional[str] = None, overrides: Optional[Dict[str, Any]] = None) -> Config:
    _load_dotenv()

    cfg_path = Path(path or os.environ.get("PILOT_CONFIG", "config.toml"))
    data: Dict[str, Any] = {}
    if cfg_path.is_file():
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))

    cfg = Config()
    cfg.config_dir = cfg_path.parent if cfg_path.is_file() else Path.cwd()

    model_tbl = data.get("model", {})
    cfg.api_key = model_tbl.get("api_key", cfg.api_key)
    cfg.base_url = model_tbl.get("base_url", cfg.base_url)
    cfg.model = model_tbl.get("name", cfg.model)
    cfg.vision_model = model_tbl.get("vision_name", cfg.vision_model)
    cfg.temperature = float(model_tbl.get("temperature", cfg.temperature))
    cfg.max_steps = int(model_tbl.get("max_steps", cfg.max_steps))

    beh = data.get("behaviour", {})
    cfg.auto_approve = _coerce_bool(beh.get("auto_approve"), cfg.auto_approve)

    br = data.get("browser", {})
    cfg.browser = BrowserConfig(
        engine=br.get("engine", cfg.browser.engine),
        channel=br.get("channel", cfg.browser.channel),
        executable_path=br.get("executable_path", cfg.browser.executable_path),
        headless=_coerce_bool(br.get("headless"), cfg.browser.headless),
        user_data_dir=br.get("user_data_dir", cfg.browser.user_data_dir),
        viewport_width=int(br.get("viewport_width", cfg.browser.viewport_width)),
        viewport_height=int(br.get("viewport_height", cfg.browser.viewport_height)),
        nav_timeout_ms=int(br.get("nav_timeout_ms", cfg.browser.nav_timeout_ms)),
    )

    # Secrets from config file.
    for name, val in data.get("secrets", {}).items():
        if isinstance(val, dict):
            cfg.secrets[name] = {k: str(v) for k, v in val.items()}
        else:
            cfg.secrets[name] = {"value": str(val)}

    # --- environment overrides ---
    cfg.api_key = os.environ.get("OPENAI_API_KEY", os.environ.get("PILOT_API_KEY", cfg.api_key))
    cfg.base_url = os.environ.get("PILOT_BASE_URL", os.environ.get("OPENAI_BASE_URL", cfg.base_url))
    cfg.model = os.environ.get("PILOT_MODEL", cfg.model)
    if os.environ.get("PILOT_VISION_MODEL"):
        cfg.vision_model = os.environ["PILOT_VISION_MODEL"]
    if os.environ.get("PILOT_BROWSER_PATH"):
        cfg.browser.executable_path = os.environ["PILOT_BROWSER_PATH"]
    if os.environ.get("PILOT_AUTO_APPROVE"):
        cfg.auto_approve = _coerce_bool(os.environ["PILOT_AUTO_APPROVE"], cfg.auto_approve)

    # Secrets from env: PILOT_SECRET_<NAME>_USER / _PASS / _VALUE
    for key, value in os.environ.items():
        if not key.startswith("PILOT_SECRET_"):
            continue
        rest = key[len("PILOT_SECRET_"):]
        for suffix, field_name in (("_USER", "username"), ("_PASS", "password"), ("_VALUE", "value")):
            if rest.endswith(suffix):
                name = rest[: -len(suffix)].lower()
                cfg.secrets.setdefault(name, {})[field_name] = value
                break

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            if hasattr(cfg, key):
                setattr(cfg, key, value)
            elif hasattr(cfg.browser, key):
                setattr(cfg.browser, key, value)

    if cfg.browser.user_data_dir:
        profile = Path(cfg.browser.user_data_dir).expanduser()
        if not profile.is_absolute():
            profile = cfg.config_dir / profile
        cfg.browser.user_data_dir = str(profile.resolve())

    return cfg
