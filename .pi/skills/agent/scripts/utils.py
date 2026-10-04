"""Small helpers shared by the other scripts: JSON results, YAML files, and where Pi and
pi-subagents keep their records."""
import io
import json
import os
import tempfile
from pathlib import Path

import typer
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import FoldedScalarString

# --- results ---------------------------------------------------------------------

_agent_dir = None  # the agent whose state results carry as `next` (report_state)


def report_state(agent_dir):
    """From now on, every result emit prints carries `next`: the agent's state and its legal moves
    (agent_state.py), so the model need not work out where the agent stands."""
    global _agent_dir
    _agent_dir = Path(agent_dir)


def emit(obj, code=0):
    if _agent_dir is not None and isinstance(obj, dict) and (_agent_dir / "plan.yaml").is_file():
        from agent_state import (
            state,  # here, not at the top: agent_state imports the validator, which imports this
        )
        try:
            obj["next"] = state(_agent_dir)
        except (OSError, ValueError, RuntimeError) as exc:  # a result must still be printed
            obj["next"] = {"error": str(exc)}
    print(json.dumps(obj, indent=2, default=str))
    raise typer.Exit(code)


def refuse(message, code=1, **extra):
    emit({"ok": False, "error": message, **extra}, code)


# --- YAML --------------------------------------------------------------------------

def rt_yaml():
    """Round-trip YAML in the plans' house style (quotes kept, no line wrapping, lists as
    `  - key:`; write_yaml folds long strings), so a load + dump changes only what was edited. It rejects repeated keys: a plain
    loader keeps the last one silently, so an edit that drops a `- id:` line would merge two list
    items."""
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_yaml(src):
    """Parse YAML text or an open file (round-trip); raises YAMLError, so a plan's errors are reported."""
    return rt_yaml().load(src)


def read_yaml(path, default):
    """A YAML file (round-trip), or `default` when it is missing, empty or invalid."""
    try:
        data = load_yaml(Path(path).read_text(encoding="utf-8"))
    except (OSError, YAMLError):
        return default
    return default if data is None else data


FOLD = 72  # a long plain string is written as a folded block (`>-`), its lines about this long (indented, about 80)


def fold_long(data):
    """`data` with each long one-line plain string as a folded block scalar, broken at single spaces (the same
    text when read back), so a plan's findings and notes read as paragraphs, not one 400-character line. Quoted
    strings, and ones already in a block, are left as they are; so is the layout around them (blank lines)."""
    if isinstance(data, (dict, list)):
        for k, v in data.items() if isinstance(data, dict) else enumerate(data):
            if (folded := fold_long(v)) is not v:
                data[k] = folded  # in place: a rebuilt list or mapping loses its comments and blank lines
                # a blank line after the value is kept as a comment that starts with the line's end; a block
                # scalar ends its own line, so that newline would add a second blank line
                after = (data.ca.items.get(k) or [None] * 4)[0 if isinstance(data, list) else 2] if hasattr(data, "ca") else None
                if after is not None and after.value.startswith("\n"):
                    after.value = after.value[1:]
        return data
    if type(data) is str and len(data) > FOLD + 12 and "\n" not in data and data == data.strip():
        folded, start = FoldedScalarString(data), 0
        folded.fold_pos = []
        for i, ch in enumerate(data):
            if ch == " " and i - start >= FOLD and data[i - 1] != " " and data[i + 1] != " ":
                folded.fold_pos.append(i)
                start = i + 1
        return folded if folded.fold_pos else data  # nowhere to break (a long path): as it was
    return data


def write_yaml(path, data, header=""):
    """Write YAML atomically (a temp file, then a rename), with `header` unless the text starts with a comment;
    long plain strings folded (fold_long)."""
    buf = io.StringIO()
    rt_yaml().dump(fold_long(data), buf)
    text = buf.getvalue()
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text if text.startswith("#") else header + text, encoding="utf-8")
    tmp.replace(path)


def read_json(path):
    """A JSON file, or None when it is missing or invalid."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- Pi and pi-subagents' records ---------------------------------------------------

def pi_agent_dir():
    """Pi's agent folder: $PI_CODING_AGENT_DIR, else ~/.pi/agent (Pi's config.js, getAgentDir)."""
    configured = os.environ.get("PI_CODING_AGENT_DIR", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".pi" / "agent"


def mission_of(run_id):
    """The id of the pi-subagents mission that lists async run `run_id`, or None. Missions are
    kept per project under <agent dir>/missions/projects/<hash>/<mission id>.json, each listing
    its runs (pi-subagents' missions/store.js)."""
    for path in (pi_agent_dir() / "missions" / "projects").glob("*/*.json"):
        mission = read_json(path) or {}
        if any(isinstance(r, dict) and r.get("runId") == run_id for r in mission.get("runs") or []):
            return mission.get("id") or path.stem
    return None


def subagents_runs_root():
    """Where pi-subagents keeps its async runs, one folder per run id (its src/shared/types.js, ASYNC_DIR)."""
    root = os.environ.get("PI_SUBAGENTS_TEMP_ROOT", "").strip()
    if not root:
        scope = f"uid-{os.getuid()}" if hasattr(os, "getuid") else f"user-{os.environ.get('USERNAME', 'shared')}"
        root = Path(tempfile.gettempdir()) / f"pi-subagents-{scope}"
    return Path(root).resolve() / "async-subagent-runs"


def subagent_records(newer_than=None):
    """(run id, status.json) of each async run pi-subagents keeps; with `newer_than` (a timestamp),
    only records written since, skipped by their file time without being read."""
    root = subagents_runs_root()
    for path in root.glob("*/status.json") if root.is_dir() else []:
        try:
            if newer_than is not None and path.stat().st_mtime < newer_than:
                continue
        except OSError:
            continue
        if isinstance(status := read_json(path), dict):
            yield path.parent.name, status


def subagent_child(run_id):
    """The child entry ({childId, state, runId, agent}) of a pi-subagents child run, from its
    workflow run's status.json `workflowChildren.children`, or None."""
    for _rid, status in subagent_records():
        for c in (status.get("workflowChildren") or {}).get("children") or []:
            if isinstance(c, dict) and c.get("runId") == run_id:
                return c
    return None


def process_alive(pid):
    """True or False for a process id on this machine; None when it cannot be told."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists, under another user
    except OSError:
        return None
    return True
