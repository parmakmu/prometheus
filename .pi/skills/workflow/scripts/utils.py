"""Small helpers with nothing specific to git, params, or plan validation,
shared by the other scripts' CLIs."""
import json
from pathlib import Path

import typer
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError


def load_yaml_safe(path, default):
    """Read-only YAML load: `default` when the file is missing or invalid."""
    try:
        data = YAML(typ="safe").load(Path(path).read_text(encoding="utf-8"))
        return default if data is None else data
    except (OSError, YAMLError):
        return default


def emit(obj, code=0):
    print(json.dumps(obj, indent=2, default=str))
    raise typer.Exit(code)
