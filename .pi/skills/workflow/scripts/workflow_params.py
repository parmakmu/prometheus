"""A workflow's param values: declared in the plan, set in the environment or `.env`.

The plan declares each param (`params:` in .pi/plan.yaml) with a type:
  path    a file or folder (the default): ~ expands, and a relative value
          resolves inside the run, so runs do not overwrite each other
  text    used exactly as given: a table name, a URL, an account id
  secret  a key, token or password. The workflow never writes it down: it stays
          in the environment, and compiled waves refer to it as ${ENV_NAME},
          which the child's shell (or a gate's) expands
A param reads the environment variable its `env:` names, else its name in upper
case (email_folder -> EMAIL_FOLDER). Values come from the process environment
first, then `.env` in the workflow root (pydantic-settings), then the plan's
default. `.env` is per machine and not in git; the workflow extension loads it
into pi's environment too, so children and gate commands see the same values.
"""
import os
import re

from dotenv import dotenv_values, set_key
from pydantic import Field, SecretStr, create_model
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = ".env"
ENV_HEADER = ("# Values for this machine; not in git. One line per param: NAME=value, where NAME is\n"
              "# the param's name in upper case or its `env:` name. The environment wins over this file.\n")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")
# Names the system, shells, Python, Node or Pi already use: a param reading one of
# them would silently pick up the machine's value (a param `user` -> $USER).
RESERVED = {"PATH", "HOME", "USER", "LOGNAME", "SHELL", "PWD", "OLDPWD", "TMPDIR", "TMP", "TEMP",
            "LANG", "TERM", "HOSTNAME", "EDITOR", "VISUAL", "PAGER", "DISPLAY", "TZ", "SHLVL",
            "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "NODE_OPTIONS", "NODE_PATH"}
RESERVED_PREFIXES = ("PI_", "LC_", "XDG_", "SSH_", "GIT_", "npm_", "UV_")


class _Base(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=True, extra="ignore",
                                      env_ignore_empty=True, env_file_encoding="utf-8")


def spec_of(spec):
    """A param's spec as a dict (the plan's raw mapping or a validator model)."""
    if hasattr(spec, "model_dump"):
        return spec.model_dump()
    return spec if isinstance(spec, dict) else {}


def kind(spec):
    return spec_of(spec).get("type") or "path"


def env_name(name, spec):
    return spec_of(spec).get("env") or name.upper()


def reserved(env):
    return env.upper() in RESERVED or env.startswith(RESERVED_PREFIXES)


def load(root, specs):
    """{name: value} for the declared params: str, SecretStr for secrets, or None when
    unset. The environment wins over `.env`; the plan's default fills the rest."""
    specs = {n: spec_of(s) for n, s in (specs or {}).items()}
    fields = {
        name: ((SecretStr if kind(s) == "secret" else str) | None, Field(None, validation_alias=env_name(name, s)))
        for name, s in specs.items() if ENV_NAME_RE.match(env_name(name, s))
    }
    settings = create_model("Params", __base__=_Base, **fields)(_env_file=os.path.join(root, ENV_FILE))
    values = {}
    for name, s in specs.items():
        value = getattr(settings, name, None)
        if value is None and kind(s) != "secret" and s.get("default") not in (None, ""):
            value = str(s["default"])
        values[name] = value
    return values


def missing(specs, values):
    """Required params with no value."""
    return [n for n, s in (specs or {}).items() if values.get(n) is None and spec_of(s).get("required", True)]


def file_keys(root):
    """The names `.env` sets (for warnings about names no param reads)."""
    path = os.path.join(root, ENV_FILE)
    return set(dotenv_values(path)) if os.path.exists(path) else set()


def resolve(name, spec, value, run_dir):
    """The text a reference ${name} compiles to."""
    if kind(spec) == "secret":
        return "${" + env_name(name, spec) + "}"  # the value stays in the environment
    value = str(value)
    if kind(spec) == "text":
        return value
    value = os.path.expanduser(value)
    if not os.path.isabs(value) and not URL_RE.match(value):
        value = os.path.join(run_dir, value)  # e.g. an `outputs` default: per run
    return value


def record(specs, values):
    """The values as a run records them: secrets only as set or not."""
    return {n: ("(secret, set)" if values.get(n) is not None else None) if kind(s) == "secret" else values.get(n)
            for n, s in (specs or {}).items()}


def set_values(root, specs, new):
    """Write {name: value} to `.env` for declared params that are not secrets;
    returns a list of problems (nothing is written when there are any)."""
    specs = {n: spec_of(s) for n, s in (specs or {}).items()}
    problems = [f"the plan declares no param '{n}'; declared: {sorted(specs)}" for n in new if n not in specs]
    problems += [f"'{n}' is a secret: the user adds {env_name(n, specs[n])} to .env (or the environment) themselves; "
                 "never ask for a secret's value" for n in new if n in specs and kind(specs[n]) == "secret"]
    if problems:
        return problems
    path = os.path.join(root, ENV_FILE)
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(ENV_HEADER)
    for n, v in new.items():
        set_key(path, env_name(n, specs[n]), str(v), quote_mode="auto")
    return []
