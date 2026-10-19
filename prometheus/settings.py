"""prometheus's settings: the folders it works in, one class (pydantic-settings) read fresh on every call.

Each comes from, in order: a value passed in (a command's flag), an environment variable, the config file
(~/.config/prometheus/config.toml, `$XDG_CONFIG_HOME` moves it; written by `prometheus init`), the default.

    agents     PROMETHEUS_AGENTS     the agents' folder                      ~/agents
    home       PROMETHEUS_HOME       the planner's folder                    ~/.prometheus
    releases   PROMETHEUS_RELEASES   where releases are unpacked and run     $XDG_DATA_HOME/prometheus/releases
    cache      PROMETHEUS_CACHE      bundles' files unpacked to rerun them    $XDG_CACHE_HOME/prometheus
    catalog    PROMETHEUS_CATALOG    the catalog file                        prometheus's own catalog.yaml

An agent's folder is not a setting: it is which agent a call acts on, and one process goes through many. Nor is Pi's
folder (PI_CODING_AGENT_DIR): it is Pi's, read only from the environment as Pi reads it, by the skill's
utils.pi_agent_dir (where pi-subagents is installed: pi_records.install).

Also: get_setting (a folder and where it came from, for init and doctor), get_agent (an agent's folder by its
slug, refused when there is none), read_config and write_config (the config file, as init writes it).
"""
import json
import os
import tomllib
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

from prometheus.errors import PrometheusError


def config_file() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "prometheus" / "config.toml"


def _xdg(name: str, fallback: str, *rest: str) -> Path:
    return Path(os.environ.get(name) or Path.home() / fallback, "prometheus", *rest)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PROMETHEUS_", env_ignore_empty=True, extra="ignore",
                                      validate_default=True)

    agents: Path = Path("~/agents")
    home: Path = Path("~/.prometheus")
    releases: Path = Field(default_factory=lambda: _xdg("XDG_DATA_HOME", ".local/share", "releases"))
    cache: Path = Field(default_factory=lambda: _xdg("XDG_CACHE_HOME", ".cache"))
    catalog: Path | None = None

    @field_validator("agents", "home", "releases", "cache", "catalog")
    @classmethod
    def _expand(cls, value):
        return value.expanduser().resolve() if value else value

    @classmethod
    def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings,
                                   file_secret_settings) -> tuple[PydanticBaseSettingsSource, ...]:
        return init_settings, env_settings, TomlConfigSettingsSource(settings_cls, toml_file=config_file())


def settings(**given) -> Settings:
    """The settings now (the environment is read on every call: tests and `init` change it); `given` (a flag) wins."""
    return Settings(**{k: v for k, v in given.items() if v})


def read_config() -> dict:
    try:
        return tomllib.loads(config_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_config(values: dict):
    path = config_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{key} = {json.dumps(value)}\n" for key, value in values.items()), encoding="utf-8")


def get_setting(name: str, flag: str | None = None) -> tuple[Path, str]:
    """A setting's folder and where it came from, for init and doctor: flag, environment, config file or default."""
    source = ("flag" if flag else "environment" if os.environ.get(f"PROMETHEUS_{name.upper()}")
              else "config" if read_config().get(name) else "default")
    return getattr(settings(**{name: flag}), name), source


def get_agent(slug: str) -> Path:
    """The agent's folder in the agents folder; refused when there is no agent by that name."""
    agents, _ = get_setting("agents")
    folder = agents / slug
    if not (folder / "plan.yaml").is_file():
        raise PrometheusError(f"no agent {slug!r} in {agents}: `prometheus list` lists the agents")
    return folder
