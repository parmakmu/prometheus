"""Small helpers shared by the other scripts: JSON results, YAML files, and where Pi keeps its files (pi-subagents'
records: pi_records.py)."""
import io
import itertools
import json
import os
import re
import tempfile
from pathlib import Path

import psutil
import typer
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError
from ruamel.yaml.scalarstring import FoldedScalarString

# --- results ---------------------------------------------------------------------

_next = None  # what every result carries as `next`, or None: set by agent_state.report_state (carry_next)


def carry_next(describe):
    """From now on, every result emit prints carries `next`: what `describe()` returns (skipped when None). The state
    machine hands it here (agent_state.report_state), so this module imports nothing above it."""
    global _next
    _next = describe


def emit(obj, code=0):
    if _next is not None and isinstance(obj, dict) and "next" not in obj:
        try:
            if (found := _next()) is not None:
                obj["next"] = found
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
    write_atomic(path, text if text.startswith("#") else header + text)


def write_atomic(path, text):
    """Write a text file whole or not at all: a temp file of its own beside it (two writers never share one), then a
    rename, so a reader in another process sees the old file or the new one."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, path.stat().st_mode & 0o777 if path.exists() else 0o644)  # mkstemp's is 0600: keep the file's own
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- an agent's plan: plan.yaml for people, .plan/ for how it was planned -----------------

PLAN_FILE = "plan.yaml"
PLAN_DIR = ".plan"
# How the plan was made, kept beside it for the planner, the critic and the scripts (plan.yaml is for people):
# each in .plan/<part>.yaml under its own key. The scripts read and write the plan as one (read_plan, write_plan).
PLAN_PARTS = {
    "unknowns": "what planning did not know, how each is resolved, and what was found",
    "questions": "what the planner asked the user, and the answers",
    "critique": "the plan critic's review and each finding's disposition (written only by agent_record_critique)",
    "agents": "the subagents the planner saw when it planned",
}
# plan.yaml's sections, in this order, each under a banner (BANNER); keys the plan does not know go last
PLAN_SECTIONS = (
    (None, ("format", "revision", "status")),
    ("What you asked for", ("request", "goal", "done_when")),
    ("What it works on", ("params", "inputs", "outputs", "network")),
    ("How it works", ("stages",)),
    ("Assumptions", ("assumptions",)),
    ("Limits and models", ("limits", "models")),
)
BANNER = "# ── "  # a line that starts so is the writer's: dropped when the plan is written again
PLAN_HEADER = "{banner}Plan for agent \"{slug}\"\n"  # a title: the schema and .plan/ are for the planner, not the reader
# editors with a YAML language server (VS Code's YAML extension, …) complete and check plan.yaml against the skill's schema
SCHEMA_LINE = "# yaml-language-server: $schema=.pi/skills/agent/plan.schema.json"


def _plan_file_data(path, strict):
    if not strict:
        return read_yaml(path, None)
    try:
        return load_yaml(Path(path).read_text(encoding="utf-8"))
    except YAMLError as exc:
        raise YAMLError(f"{Path(path).parent.name}/{Path(path).name}: {exc}") from exc


def read_plan(agent_dir, default=None, strict=False):
    """The whole plan: plan.yaml with each .plan/<part>.yaml merged in, as one mapping. `strict`: a file that
    can't be read raises (OSError, or YAMLError naming the file) instead of giving `default`."""
    agent_dir = Path(agent_dir)
    if not strict and not (agent_dir / PLAN_FILE).is_file():
        return default
    plan = _plan_file_data(agent_dir / PLAN_FILE, strict)
    if not isinstance(plan, dict):
        return default if plan is None else plan
    for part in PLAN_PARTS:
        f = agent_dir / PLAN_DIR / f"{part}.yaml"
        if f.is_file() and isinstance(data := _plan_file_data(f, strict), dict) and part in data:
            plan[part] = data[part]
    return plan


def parts_in_plan_file(agent_dir):
    """The planning parts plan.yaml itself holds (they belong in .plan/): written there by hand."""
    data = read_yaml(Path(agent_dir) / PLAN_FILE, {})
    return [p for p in PLAN_PARTS if isinstance(data, dict) and p in data]


def plan_text(plan, slug):
    """plan.yaml's text: its sections in order, each under a banner, the planning parts left out."""
    known = {k for _, keys in PLAN_SECTIONS for k in keys}
    rest = tuple(k for k in plan if k not in known and k not in PLAN_PARTS)
    chunks = [PLAN_HEADER.format(banner=BANNER, slug=slug) + SCHEMA_LINE]
    for title, keys in (*PLAN_SECTIONS, ("Other", rest)):
        entries = []
        for k in (k for k in keys if k in plan):
            buf = io.StringIO()
            rt_yaml().dump(fold_long(CommentedMap([(k, plan[k])])), buf)
            # a banner read back sticks to the value before it: the writer's own lines go, the rest stays
            body = "\n".join(line for line in buf.getvalue().splitlines() if not line.startswith((BANNER, SCHEMA_LINE))).strip("\n")
            if k == "stages":  # each stage its own paragraph
                body = re.sub(r"\n+(?=  - id: )", "\n\n", body).replace("stages:\n\n", "stages:\n")
            entries.append(body)
        if entries:  # a blank line around anything longer than a line
            text = entries[0]
            for prev, cur in itertools.pairwise(entries):
                text += ("\n" if "\n" not in prev and "\n" not in cur else "\n\n") + cur
            chunks.append((f"{BANNER}{title} " + "─" * (72 - len(title)) + "\n" if title else "") + text)
    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(chunks)) + "\n"


def stage_kind(stage):
    """A stage's kind (deterministic, judgment, decision, side_effect): `kind: {value, reason}` in the plan."""
    kind = stage.get("kind") if isinstance(stage, dict) else None
    return kind.get("value") if isinstance(kind, dict) else None


def write_plan(agent_dir, plan):
    """Write the whole plan: the planning parts each to .plan/<part>.yaml (removed when empty), the rest to
    plan.yaml in its sections (plan_text). Atomic per file."""
    agent_dir = Path(agent_dir)
    for part, what in PLAN_PARTS.items():
        f = agent_dir / PLAN_DIR / f"{part}.yaml"
        value = plan.get(part)
        if value is None or value == [] or value == {}:
            f.unlink(missing_ok=True)
            continue
        f.parent.mkdir(exist_ok=True)
        write_yaml(f, CommentedMap([(part, value)]), header=f"{BANNER}{part}: {what}.\n{BANNER}Part of the plan of agent \"{agent_dir.name}\" (plan.yaml); read with it.\n")
    write_atomic(agent_dir / PLAN_FILE, plan_text(plan, agent_dir.name))


def read_json(path):
    """A JSON file, or None when it is missing or invalid."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- what looks like a secret (prometheus's share.py before a push; run_bundle.py) ------

# provider keys and tokens, private keys
SECRET_PATTERNS = [
    (re.compile(r"\b(sk-(ant-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|glpat-[A-Za-z0-9_-]{20,}|"
                r"xox[abpr]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{35})\b"), "an API key or token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
]


# --- the agent ------------------------------------------------------------------------

def agent_name(agent_dir):
    """The agent's name: its folder's (agents/<slug>), or in an unpacked release (releases/<slug>/<version>, named by
    version) the one its RELEASE.json records."""
    release = read_json(Path(agent_dir) / "RELEASE.json")
    return release.get("agent") if isinstance(release, dict) and release.get("agent") else Path(agent_dir).resolve().name


# --- Pi ---------------------------------------------------

def pi_agent_dir():
    """Pi's agent folder: $PI_CODING_AGENT_DIR, else ~/.pi/agent (Pi's config.js, getAgentDir)."""
    configured = os.environ.get("PI_CODING_AGENT_DIR", "").strip()
    return Path(configured).expanduser() if configured else Path.home() / ".pi" / "agent"


# a process created this long after the one recorded started is another that reused its pid
PID_REUSE_SLACK_S = 5


def process_alive(pid, started=None):
    """True or False for a process id on this machine; None when it cannot be told. `started` (a Unix time): when
    the recorded process started; a process with that pid created later is another one, and the recorded one is gone."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    try:
        process = psutil.Process(pid)
        if process.status() == psutil.STATUS_ZOMBIE:  # it ended; its parent hasn't reaped it
            return False
        return not (started is not None and process.create_time() > started + PID_REUSE_SLACK_S)
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True  # it exists, under another user
    except psutil.Error:
        return None


def this_process():
    """This process as a run records its driver: its pid, and when it started (process_alive tells a reused pid)."""
    return {"pid": os.getpid(), "started": psutil.Process().create_time()}


def package_name(requirement):
    """The normalised name of a requirement (`Pandas[excel]>=2` -> `pandas`); a bare name is normalised too.
    Raises InvalidRequirement for what isn't one."""
    return canonicalize_name(Requirement(requirement).name)
