"""prometheus's settings: the folders it works in, one class (pydantic-settings) read fresh on every call.

Each comes from, in order: a value passed in (a command's flag), an environment variable, the config file
(~/.config/prometheus/config.toml, `$XDG_CONFIG_HOME` moves it; written by `prometheus init`), the default.

    agents     PROMETHEUS_AGENTS     the agents' folder                      ~/agents
    home       PROMETHEUS_HOME       the planner's folder                    ~/.prometheus
    releases   PROMETHEUS_RELEASES   where releases are unpacked and run     $XDG_DATA_HOME/prometheus/releases
    cache      PROMETHEUS_CACHE      bundles' files unpacked to rerun them    $XDG_CACHE_HOME/prometheus
    catalog    PROMETHEUS_CATALOG    the catalog file                        prometheus's own catalog.yaml
    pi_agent   PI_CODING_AGENT_DIR   Pi's global folder (where pi-subagents is installed)   ~/.pi/agent

An agent's folder is not a setting: it is which agent a call acts on, and one process goes through many.
The skill's scripts read PI_CODING_AGENT_DIR themselves (utils.pi_agent_dir): an agent runs without prometheus.
"""
import os
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


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
    pi_agent: Path = Field(default_factory=lambda: Path.home() / ".pi" / "agent", validation_alias="PI_CODING_AGENT_DIR")

    @field_validator("agents", "home", "releases", "cache", "catalog", "pi_agent")
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
