#!/usr/bin/env python3
"""Validate an agent's plan.yaml against the plan schema, the
pi-subagents constraints it will be compiled into, and (when the plan lives in
an agent) the agent's portability.

Usage:
  plan_validate.py PLAN.yaml [--agents a,b,c] [--external x,y] [--agent DIR]

In an agent, stage status comes from the active run (runs/<id>/run.yaml; see
run_folders.py): the plan is reusable, each run is one execution of it.

--agents    agent names from subagent({ action: "list" }); overrides the
            plan's `agents.available` list
--external  names among them whose profile uses an external runner
--agent     agent root; defaults to the plan's folder when it holds a `.pi/`
            (the plan is at <agent>/plan.yaml)

Two passes: the pydantic models below check the plan's shape (types, required
fields, allowed values); the rules in validate() check how the parts fit
together, on a plan whose shape is known to be right.

Prints one JSON object {valid, status, errors, warnings, summary} and exits
0 when there are no errors, 1 otherwise (2 on usage or read errors).
Requires typer, pydantic and ruamel.yaml (`uv sync`).
"""
import ast
import contextlib
import hashlib
import json
import re
import sys
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import Annotated, Any, Literal

import agent_params
import typer
from packaging.requirements import InvalidRequirement
from packaging.utils import canonicalize_name
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    ValidationError,
    model_validator,
)
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from run_folders import active_run, last_run
from utils import (
    PLAN_FILE,
    load_yaml,
    package_name,
    parts_in_plan_file,
    read_plan,
    stage_kind,
)


def plan_digest(plan):
    """A hash of the plan's content, not its bookkeeping (status, revision, critique): what a critic's result is for."""
    content = {k: v for k, v in plan.items() if k not in ("status", "revision", "critique")}
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()[:16]


def critic_required(plan):
    """Whether the plan needs the critic: more than 3 stages, or a side effect or judgment (SKILL.md)."""
    stages = [s for s in plan.get("stages") or [] if isinstance(s, dict)]
    return len(stages) > 3 or any(stage_kind(s) in ("side_effect", "judgment") for s in stages)


def check_ready(plan, base=None):
    """Why the plan cannot run, or None: its status is not ready, or the critic's result was not recorded for
    exactly this plan (critique.plan_digest, which only agent_record_critique writes: a critique written by
    hand, or a plan edited after it, is not one). `base`: main's plan, on a branch; a plan that differs from
    it needs that too, even one small enough to need no critic."""
    if plan.get("status") != "ready":
        return f"the plan is {plan.get('status')}, not ready: finish it first (the critic, then agent_record_critique)"
    critique = plan.get("critique") if isinstance(plan.get("critique"), dict) else {}
    seen, digest = critique.get("plan_digest"), plan_digest(plan)
    if seen == digest:
        return None
    if seen:
        return ("plan.yaml changed after the critic's result was recorded, so the critic has not seen this plan: "
                "run the critic on it and record the result (agent_record_critique)")
    if critique or critic_required(plan) or (base is not None and plan_digest(base) != digest):
        return ("the critic's result for this plan was not recorded with agent_record_critique, which checks the critic "
                "ran and ties its result to the plan: run the critic and record it (never set status: ready by hand)")
    return None


MULTI_CHILD = {"map", "map_reduce"}
DOC_FORMATS = {"md", "markdown", "txt", "docx", "html", "pdf"}
GATES_REF_RE = re.compile(r"\.\./gates/([^\s'\"]+)")
PARAM_REF_RE = re.compile(r"\$\{([^}]*)\}")
SCRIPT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
PATH_CHARS = set("/\\.~_-") | set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
ABS_PATH_RE = re.compile(r"(^|[\s'\"=])(/[^\s'\"]+|~[^\s'\"]*|[A-Za-z]:[\\/][^\s'\"]*)")

# Launch context pi-subagents' builtin agents use when `context` is omitted.
SHELL_BUILTINS = {"scout", "worker", "delegate", "oracle"}  # pi-subagents' builtins with bash
BUILTIN_CONTEXT = {
    "oracle": "fork",
    "worker": "fresh",
    "reviewer": "fresh",
    "scout": "fresh",
    "researcher": "fresh",
    "evidence-auditor": "fresh",
}

# --- shape ------------------------------------------------------------------

Str = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]*$")]
ParamName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
Count = Annotated[int, Field(ge=0)]
Context = Literal["fresh", "fork"]


class M(BaseModel):
    """A plan section: known fields are typed, others pass through (notes, target, ...)."""
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _drop_nulls(cls, data):
        # `field:` with no value means "not set"
        return {k: v for k, v in data.items() if v is not None} if isinstance(data, dict) else data


class Param(M):
    description: Str
    type: Literal["path", "text", "secret"] = "path"  # see agent_params.py
    env: Str | None = None  # the environment variable; default: the name in upper case
    default: Any = None
    required: StrictBool = True
    per_run: StrictBool = False  # asked for each run (a query): kept on the run, never in .env


class Input(M):
    id: Str
    kind: Literal["folder", "file", "app", "url", "text"]
    path: str | None = None
    access: Literal["read", "read_write"] = "read"


class Output(M):
    id: Str
    kind: Literal["file", "app", "message"]
    path: Str
    overwrite: StrictBool = False


class AgentEntry(M):
    name: Str
    source: Literal["builtin", "package", "project", "user"] = "builtin"
    runner: Literal["native", "external-cli"] | None = None
    context: Context | None = None

    @model_validator(mode="before")
    @classmethod
    def _bare_name(cls, data):
        return {"name": data} if isinstance(data, str) else data


class Agents(M):
    available: list[AgentEntry] = []


class StageOut(M):
    id: Str
    path: str | None = None
    format: str | None = None
    schema_: Any = Field(None, alias="schema")


class Kind(M):
    """What sort of work a stage is (references/classify.md), and why."""
    value: Literal["deterministic", "judgment", "decision", "side_effect"] = Field(
        description="deterministic: a program can do it and check it; judgment: it needs reading; decision: the user "
                    "chooses; side_effect: it changes something outside the run (the user approves it first)")
    reason: str | None = Field(None, description="why this kind, in a line (for judgment: why a program can't decide)")

    @model_validator(mode="before")
    @classmethod
    def _a_mapping(cls, data):
        if isinstance(data, str):
            raise ValueError(f"kind is a mapping: {{value: {data}, reason: <why>}}")  # noqa: TRY004 (pydantic reports a ValueError, not a TypeError)
        return data


class Program(M):
    """A stage's program, kept in the agent's scripts/ and reused by later runs."""
    file: Str = Field(description="its file name in the agent's scripts/")
    args: str | None = Field(None, description="its arguments; with them, runs execute it as a command (no model)")


class Map(M):
    """How a map or map_reduce stage splits its work."""
    over: str | None = Field(None, description="the input or output it reads whose items are split into batches")
    glob: str | None = Field(None, description="which files of a folder are its items, e.g. \"*.eml\"")
    batch: int | None = Field(None, description="items per child")


class A2ACall(M):
    """A stage that calls another agent over A2A (pattern: a2a): the batcher sends it a message and downloads its
    artifacts into the stage's out path."""
    url: Str  # the agent's address (its Agent Card is at <url>/.well-known/agent-card.json), usually a ${param}
    files: dict[str, str] = {}  # the remote agent's path param -> an id this stage reads (a file or folder)
    params: dict[str, Any] = {}  # the remote agent's text params
    approve: list[str] = []  # the remote agent's side effects this stage approves (then it is a side effect itself)
    token: str | None = None  # "${secret param}": sent as Authorization: Bearer
    timeout_s: int | None = None  # how long to wait for the remote run (default: limits.command_timeout_s, else 600)


class Stage(M):
    """A unit of work (references/plan-schema.md, "Stages")."""
    id: Key = Field(description="unique, lower case with dashes: names its children and what runs after it")
    kind: Kind
    title: str | None = Field(None, description="a short label")
    pattern: Literal["script", "map", "map_reduce", "scout", "worker", "reviewer", "approval", "a2a"] | None = Field(
        None, description="how it runs: script (a program), map / map_reduce (a child per batch), scout, worker, "
                          "reviewer, approval (asks the user), a2a (calls another agent)")
    agent: str | None = Field(None, description="the subagent its child runs as")
    resumes: str | None = Field(None, description="an earlier single-child stage whose child it continues")
    context: Context | None = Field(None, description="fresh or fork; only when not the subagent's default")
    resolves_after: Key | None = Field(None, description="an outline until this discovery stage has run; the run then resolves it, writing the stage out in full")
    intent: str | None = Field(None, description="provisional: what it must produce")
    instructions: str | None = Field(None, description="what a model child must do and how, in full: its brief")
    after: list[str] = Field([], description="the stages that must finish first")
    reads: list[str] = Field([], description="input ids, or what stages it runs after write")
    writes: list[StageOut] = Field([], description="what it writes: a file (with path) or its returned result")
    done_when: str | None = Field(None, description="how its result is judged done")
    gate: Any = Field(None, description="a command that checks its output (../gates/<script>), or {command, output: json}")
    review: str | None = Field(None, description="the subagent that reviews its result (adds an <id>-review child)")
    pauses_after: StrictBool = Field(False, description="the user sees its results before the stages after it run")
    map: Map | None = Field(None, description="map / map_reduce: what is split, and how")
    program: Program | None = Field(None, description="its program in scripts/ and the arguments it runs with")
    model: Str | None = Field(None, description="the model its children use (default: models.default, else the subagent's)")
    reuse: StrictBool = Field(True, description="false: always run again, never take an earlier run's result")
    a2a: A2ACall | None = Field(None, description="pattern a2a: the agent it calls")


class Unknown(M):
    id: Str
    text: Str
    resolved_by: Literal["planner", "probe", "user", "discovery"]
    finding: Any = None
    run_id: str | None = None
    question: str | None = None
    stage: str | None = None
    affects: list[str] = []


class Assumption(M):
    id: Str
    text: Str
    source: Literal["observed", "default", "answer"] | None = None
    confirm: StrictBool = False


class Finding(M):
    id: Str
    severity: Literal["blocking", "major", "minor"]
    claim: Str
    disposition: Literal["accepted", "rejected", "asked"]
    note: str | None = None


class Critique(M):
    agent: Str
    run_id: Str
    verdict: Literal["pass", "revise", "ask_user"]
    passes: Annotated[int, Field(ge=1, le=2)] = 1
    revision: int | None = None
    plan_digest: str | None = None  # plan_digest() of the plan it was recorded for (plan_edit.py critique)
    findings: list[Finding] = []


class Question(M):
    id: Str
    question: Str
    why: Str
    default: Any
    options: Annotated[list[Any], Field(min_length=2)]
    blocks: Annotated[list[str], Field(min_length=1)]
    from_finding: str | None = None
    answer: Any = None


class Limits(M):
    max_parallel: Count | None = None
    max_retries: Count | None = None
    max_batch: Count | None = None
    command_timeout_s: Annotated[int, Field(gt=0)] | None = None  # a stage program or gate run as a command (default 600)
    max_cost_usd: Annotated[float, Field(gt=0)] | None = None  # the run's children may spend this much before the user is asked


class Models(M):
    """Models the children use, by role; a stage's `model` wins over `default`. Cheap ones for reviews pay off most."""
    default: Str | None = None
    review: Str | None = None


class Plan(M):
    format: Literal[1]
    status: Literal["draft", "needs_answers", "ready"]
    revision: Annotated[int, Field(ge=1)] = 1
    request: Str
    goal: Str
    done_when: Annotated[list[Str], Field(min_length=1)]
    params: dict[ParamName, Param] = {}
    inputs: list[Input] = []
    outputs: list[Output] = []
    agents: Agents | None = None
    unknowns: list[Unknown] = []
    stages: Annotated[list[Stage], Field(min_length=1)]
    assumptions: list[Assumption] = []
    questions: list[Question] = []
    critique: Critique | None = None
    limits: Limits = Limits()
    models: Models = Models()
    # hosts the agent's stages call (APIs, sites): with SANDBOX=podman, only hosts you allowed are reachable, and these
    # are offered to you to allow; a leading "." covers subdomains
    network: list[Annotated[str, StringConstraints(pattern=r"^\.?([A-Za-z0-9-]+\.)+[A-Za-z]{2,}$")]] = []


def plan_json_schema():
    """plan.yaml's JSON Schema, for editors (the skill's plan.schema.json; tests/test_validator.py checks it is current):
    the shape the models above check, with a stage's fields closed, as validate() does."""
    schema = Plan.model_json_schema()
    schema["$defs"]["Stage"]["additionalProperties"] = False
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "prometheus plan.yaml", **schema}


LABELS = {"inputs": "input", "outputs": "output", "stages": "stage", "unknowns": "unknown",
          "assumptions": "assumption", "questions": "question", "params": "param"}


def shape_errors(exc, raw):
    """pydantic errors, with list items named by their key or id: "stage 'x': kind: Input should be ..."."""
    out = []
    for e in exc.errors():
        loc, head = list(e["loc"]), ""
        if len(loc) > 1 and loc[0] in LABELS:
            item = loc[1]
            if isinstance(item, int):
                try:
                    entry = raw[loc[0]][item]
                    name = entry.get("id") if isinstance(entry, dict) else None
                except (KeyError, IndexError, TypeError):
                    name = None
                head = f"{LABELS[loc[0]]} '{name}'" if name else f"{loc[0]}[{item}]"
            else:
                head = f"{LABELS[loc[0]]} '{item}'"
            loc = loc[2:]
        field = "".join(f"[{p}]" if isinstance(p, int) else ".name" if p == "[key]" else f".{p}" for p in loc).lstrip(".")
        out.append(": ".join(x for x in (head, field, e["msg"]) if x))
    return out


# --- helpers ------------------------------------------------------------------

def is_run_stage(st):
    """Stages that launch a child. Decisions and approvals are parent pauses."""
    return st.kind.value != "decision" and st.pattern != "approval"


def human_first(st):
    """Stages the parent handles with the user before a wave's script starts."""
    return st.kind.value in ("decision", "side_effect") or st.pattern == "approval"


def derive_waves(by_key, after, status, first=1):
    """Group the stages that are not done into waves, each stage as early as `after` allows.

    A workflow script cannot pause, so a wave ends wherever the parent must act
    between two stages. A stage goes in a later wave than a stage it runs after when
    - it is a decision, approval or side effect and the needed stage launches a
      child (the parent asks the user before the wave's script starts);
    - that stage is a side effect (its results are reported first) or has
      `pauses_after` (the user reviews it first);
    - it is provisional until that stage, a discovery (the parent resolves in between; this counts
      as `after`).
    `status` maps keys to running | done (absent = pending).
    Returns ([{id, stages, ends_with}], {key: wave index}); raises CycleError."""
    active = [k for k in by_key if status.get(k) != "done"]
    deps = {k: {d for d in after[k] if d in active} for k in active}
    for k in active:
        st = by_key[k]
        if st.resolves_after in deps:
            deps[k].add(st.resolves_after)
    level = {}
    for k in TopologicalSorter(deps).static_order():
        st = by_key[k]
        level[k] = max((level[d] + int(bool(
            by_key[d].kind.value == "side_effect" or by_key[d].pauses_after
            or (human_first(st) and is_run_stage(by_key[d]))
            or d == st.resolves_after))
            for d in deps[k]), default=0)
    waves = []
    for i in range(max(level.values(), default=-1) + 1):
        members = [k for k in active if level[k] == i]  # plan order
        ends_with = "none"
        if any(by_key[k].kind.value == "side_effect" for k in members):
            ends_with = "approval"
        if any(by_key[k].pauses_after for k in members):
            ends_with = "review"
        if any(by_key[p].resolves_after in members for p in active):
            ends_with = "resolve"
        waves.append({"id": f"w{first + i}", "stages": members, "ends_with": ends_with})
    return waves, level


def commanded(st):
    """A stage whose program runs as a command on the batcher (no model): it has a program with args."""
    return st.program is not None and st.program.args is not None


def child_keys(key, pattern):
    if pattern == "map":
        return f"{key}-split, {key}-<n>, {key}-merge"
    if pattern == "map_reduce":
        return f"{key}-split, {key}-<n>, {key}-reduce"
    return key


SCRIPT_BLOCK_RE = re.compile(r"^# /// script\s*$", re.MULTILINE)
# import names whose package is called something else
IMPORT_PACKAGES = {"yaml": "pyyaml", "bs4": "beautifulsoup4", "PIL": "pillow", "dotenv": "python-dotenv",
                   "sklearn": "scikit-learn", "cv2": "opencv-python", "dateutil": "python-dateutil",
                   "docx": "python-docx", "pptx": "python-pptx", "ruamel": "ruamel.yaml", "jwt": "pyjwt",
                   "Crypto": "pycryptodome", "fitz": "pymupdf", "magic": "python-magic"}


def project_packages(root: Path):
    """Normalised names of the packages the agent's pyproject.toml lists, or None if unreadable."""
    try:
        import tomllib
        deps = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    except (ImportError, OSError, KeyError, TypeError, ValueError):
        return None
    names = set()
    for d in deps:
        with contextlib.suppress(InvalidRequirement, TypeError):
            names.add(package_name(d))
    return names


def package_problem(script: Path, packages, runs_with_uv: bool):
    """Why a Python gate or stage program would fail before doing its work, or None. Its packages
    are the agent's, listed in pyproject.toml (added with `agent_add_package`); `uv run` syncs that
    environment before running the script."""
    try:
        text = script.read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError, ValueError) as exc:
        return f"cannot be parsed: {exc}"
    if SCRIPT_BLOCK_RE.search(text):
        return "declares packages in a `# /// script` block; list them in the agent's pyproject.toml instead (`agent_add_package`) and remove the block"
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    third_party = sorted(n for n in names if n not in sys.stdlib_module_names and not (script.parent / f"{n}.py").exists())
    if third_party and packages is not None:
        missing = [n for n in third_party if canonicalize_name(IMPORT_PACKAGES.get(n, n)) not in packages]
        if missing:
            return (f"imports {', '.join(missing)}, which the agent's pyproject.toml does not list: "
                    f"add {' '.join(IMPORT_PACKAGES.get(n, n) for n in missing)} with agent_add_package (the user approves it, so give the reason)")
    if third_party and not runs_with_uv:
        return f"imports {', '.join(third_party)}, so its command must run it with `uv run` (the agent's environment), not python3"
    return None


def literal_absolute(text):
    """True when a path or command contains an absolute path not rooted in a ${param}."""
    if not isinstance(text, str):
        return False
    return bool(ABS_PATH_RE.search(PARAM_REF_RE.sub("PARAM", text)))


def duplicates(values):
    seen, dup = set(), []
    for v in values:
        if v in seen:
            dup.append(v)
        seen.add(v)
    return dup


def read_frontmatter(path: Path):
    """An agent file's frontmatter as a mapping ({} when it has none or cannot be read)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    end = text.find("\n---", 3) if text.startswith("---") else -1
    if end == -1:
        return {}
    try:
        meta = YAML(typ="safe").load(text[3:end]) or {}
    except YAMLError:
        return {}
    return meta if isinstance(meta, dict) else {}


def project_subagents(root: Path):
    """{name: frontmatter} of the agents in the agent's .pi/agents/."""
    out = {}
    for path in (Path(root) / ".pi" / "agents").rglob("*.md"):
        meta = read_frontmatter(path)
        out[meta.get("name") or path.stem] = meta
    return out


def project_agent_names(root: Path):
    return set(project_subagents(root))


def agent_tools(meta):
    """An agent's `tools` (a list, or a comma-separated string) as a list of names."""
    tools = meta.get("tools")
    return [str(x).strip() for x in (tools.split(",") if isinstance(tools, str) else tools or []) if str(x).strip()]


def load_agents(plan, cli_agents, cli_external, warn):
    """Return (names or None, external set, default-context map, source map)."""
    entries = plan.agents.available if plan.agents else []
    external = {a.strip() for a in (cli_external or "").split(",") if a.strip()}
    sources = {a.name: a.source for a in entries}
    contexts = BUILTIN_CONTEXT | {a.name: a.context for a in entries if a.context}
    if cli_agents is not None:
        return {a.strip() for a in cli_agents.split(",") if a.strip()} | external, external, contexts, sources
    external |= {a.name for a in entries if a.runner == "external-cli"}
    if plan.agents is None:
        warn("no agents block and no --agents: agent names were not checked against subagent list")
        return None, external, contexts, sources
    if not entries:
        warn("agents.available is empty: agent names were not checked")
        return None, external, contexts, sources
    return {a.name for a in entries}, external, contexts, sources


# --- rules --------------------------------------------------------------------

def validate(raw, cli_agents=None, cli_external=None, agent_root=None, run_state=None):
    """(errors, warnings, summary) for a plan. `run_state`: the run whose stage status applies
    (a run.yaml mapping); in an agent, default the active run. Without one, every stage is
    pending."""
    if not isinstance(raw, dict):
        return ["plan must be a YAML mapping"], [], {}
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as exc:
        return shape_errors(exc, raw), [], {}
    c = Checker(plan, raw, cli_agents, cli_external, agent_root, run_state)
    for rules in (c.limits, c.inputs, c.stage_index, c.graph, c.stages, c.outputs, c.outside_readers, c.side_effect_reach, c.run_and_waves,
                  c.unknowns, c.assumptions, c.critique, c.questions, c.params, c.agent_folder):
        rules()
    return c.errors, c.warnings, c.summary()


class Checker:
    """The cross-reference rules, run on a plan whose shape pydantic accepted. Each method is
    one group of rules; `validate` runs them in order, since later groups use what earlier
    ones found (the stage index, the dependency graph, the run's stage status)."""

    def __init__(self, plan, raw, cli_agents, cli_external, agent_root, run_state):
        self.plan, self.raw, self.run_state = plan, raw, run_state
        self.agent_root = Path(agent_root) if agent_root else None
        self.errors, self.warnings = [], []
        self.err, self.warn = self.errors.append, self.warnings.append
        self.param_refs = []  # (where, name, is a path, follows path text such as `../`)
        self.agent_names, self.external, self.default_ctx, self.agent_sources = load_agents(
            plan, cli_agents, cli_external, self.warn)
        self.input_ids = {inp.id for inp in plan.inputs}
        self.approvals, self.keys_summary = [], {}
        self.gate_scripts = []  # (where, file under <agent>/gates/, its command runs uv)
        self.script_owner = {}  # stage program -> the stage it belongs to

    def check_refs(self, where, text, is_path=False, secret_ok=False):
        """`secret_ok`: a command the batcher runs (a stage program's args and its gate), the one place a secret reaches."""
        if isinstance(text, str):
            self.param_refs.extend((where, m.group(1), is_path, m.start() > 0 and text[m.start() - 1] in PATH_CHARS, secret_ok)
                                   for m in PARAM_REF_RE.finditer(text))

    def check_agent_ref(self, where, name):
        if self.agent_names is not None and name not in self.agent_names:
            self.err(f"{where}: agent '{name}' is not in the available agents")
        if self.agent_sources.get(name) == "user":
            self.err(f"{where}: agent '{name}' is user-scoped and will not exist where the agent is copied; copy its file to .pi/agents/ and set source: project")

    def limits(self):
        for k in self.plan.limits.model_extra or {}:
            self.warn(f"limits.{k} is not a known limit")
        for k in self.plan.models.model_extra or {}:
            self.warn(f"models.{k} is not a known role (default, review)")

    def inputs(self):
        for iid in duplicates(inp.id for inp in self.plan.inputs):
            self.err(f"input '{iid}': duplicate id")
        for inp in self.plan.inputs:
            where = f"input '{inp.id}'"
            if not inp.path and inp.kind != "text":
                self.err(f"{where}: path is required")
            self.check_refs(where, inp.path, is_path=inp.kind in ("folder", "file"))
            if inp.kind in ("folder", "file") and literal_absolute(inp.path):
                self.warn(f"{where}: absolute path; use a ${{param}} so the agent can be moved")

    def stage_index(self):
        """Stages by id, each output's producer, and child keys that would collide."""
        self.by_key, self.producer = {}, {}
        for st in self.plan.stages:
            if st.id in self.by_key:
                self.err(f"stage '{st.id}': duplicate id"); continue
            self.by_key[st.id] = st
            for o in st.writes:
                if o.id in self.input_ids:
                    self.err(f"stage '{st.id}': writes '{o.id}', an input's id")
                if o.id in self.producer:
                    self.err(f"stage '{st.id}': writes '{o.id}', which stage '{self.producer[o.id]}' already writes")
                else:
                    self.producer[o.id] = st.id
        self.provisional = {k for k, st in self.by_key.items() if st.resolves_after}
        for key, st in self.by_key.items():
            reserved = {
                "map": rf"^{re.escape(key)}-(\d+|split|merge)$",
                "map_reduce": rf"^{re.escape(key)}-(\d+|split|reduce)$",
            }.get(st.pattern)
            for other in self.by_key:
                if reserved and other != key and re.match(reserved, other):
                    self.err(f"stage '{other}' collides with child keys of {st.pattern} stage '{key}' ({child_keys(key, st.pattern)})")
            if st.review and f"{key}-review" in self.by_key:
                self.err(f"stage '{key}-review' collides with the review child the runner adds for stage '{key}' (review)")
            if commanded(st) and f"{key}-fix" in self.by_key:
                self.err(f"stage '{key}-fix' collides with the child that fixes stage '{key}''s program if it fails (program.args)")

    def graph(self):
        """after, each stage's ancestors, whether there is a cycle, and what reviewers cover."""
        self.after = {}
        for key, st in self.by_key.items():
            for dep in st.after:
                if dep == key:
                    self.err(f"stage '{key}': runs after itself")
                elif dep not in self.by_key:
                    self.err(f"stage '{key}': after unknown stage '{dep}'")
            self.after[key] = [d for d in st.after if d in self.by_key and d != key]
        self.ancestors, self.cyclic = {}, False
        try:
            for k in TopologicalSorter(self.after).static_order():
                self.ancestors[k] = set(self.after[k]).union(*(self.ancestors[d] for d in self.after[k]))
        except CycleError as exc:  # args[1] runs dependency -> dependent; show it in run order reversed
            self.cyclic = True
            self.err("dependency cycle: " + " -> ".join(reversed(exc.args[1])))
        self.reviewed = set().union(*(self.ancestors.get(k, set()) for k, st in self.by_key.items() if st.pattern == "reviewer"))

    # --- one stage ----------------------------------------------------------------
    def stages(self):
        for key, st in self.by_key.items():
            self.stage(key, st)

    def stage(self, key, st):
        where = f"stage '{key}'"
        self.stage_common(where, key, st)
        if key in self.provisional:
            self.keys_summary[key] = "(provisional)"
            self.stage_provisional(where, st)
            return  # detail rules apply once the stage is concrete
        run = is_run_stage(st)
        if run:
            self.keys_summary[key] = child_keys(key, st.pattern)
        eff_agent = self.stage_agent(where, key, st, run)
        is_external = eff_agent in self.external
        structured = self.stage_outputs(where, key, st, run, eff_agent, is_external)
        self.stage_gate(where, st, run, structured, eff_agent, is_external)
        if st.pattern in ("map", "map_reduce"):
            self.stage_map(where, st)

    def stage_common(self, where, key, st):
        """Rules for every stage, provisional or not."""
        if st.pattern is None and key not in self.provisional:
            self.err(f"{where}: pattern is required")
        if not st.title:
            self.warn(f"{where}: no title")
        if st.kind.value == "side_effect":
            self.approvals.append(key)
        for cid in st.reads:
            if cid in self.input_ids:
                continue
            if cid not in self.producer:
                self.err(f"{where}: reads unknown id '{cid}'")
            elif not self.cyclic and self.producer[cid] not in self.ancestors.get(key, set()):
                self.err(f"{where}: reads '{cid}' from stage '{self.producer[cid]}', which it does not run after (directly or transitively)")
        for o in st.writes:
            if o.path:
                self.check_refs(f"{where} writes '{o.id}'", o.path, is_path=True)
                if literal_absolute(o.path):
                    self.err(f"{where}: writes '{o.id}' to an absolute path; use a ${{param}} or a relative path (it resolves in the run: runs/<id>/work/)")
        program, kind = st.program, st.kind.value
        if program is not None:
            if not SCRIPT_NAME_RE.match(program.file):
                self.err(f"{where}: program file '{program.file}' must be a file name in the agent's scripts/ (no folders)")
            if kind not in ("deterministic", "side_effect"):
                self.err(f"{where}: a program is for deterministic and side-effect stages; a {kind} stage has none")
            if st.pattern in MULTI_CHILD:
                self.err(f"{where}: a program on a {st.pattern} stage is not supported yet: every batch child would write it")
            elif st.pattern != "script":
                self.err(f"{where}: a stage with a program has pattern: script (not {st.pattern})")
            if program.file in self.script_owner:
                self.err(f"{where}: program '{program.file}' is also stage '{self.script_owner[program.file]}'s; give each stage its own")
            self.script_owner.setdefault(program.file, st.id)
            if program.args is None and st.pattern == "script":
                self.warn(f"{where}: give the program its `args`, so runs execute it as a command, "
                          "without a model; otherwise a model child runs it every run")
            self.check_refs(f"{where} program.args", program.args, secret_ok=True)
            returned = [o.id for o in st.writes if not o.path]
            if program.args is not None and returned:
                self.err(f"{where}: a program run as a command writes files and returns nothing; give writes {returned} a path")
        elif kind == "deterministic" and st.pattern == "script":
            self.warn(f"{where}: name its program with `program: {{file, args}}` (kept in scripts/, reviewed, reused by later runs); "
                      "without it every run writes the program again")
        elif kind == "deterministic" and st.pattern in ("worker", "scout", "reviewer"):
            self.warn(f"{where}: deterministic work in a {st.pattern} child; make it pattern: script with `program: {{file, args}}`, "
                      "so the program is kept in scripts/ and later runs execute it without a model")
        self.stage_a2a(where, st)
        for field in sorted(set(st.model_extra or {}) - {"status"}):  # status: run_and_waves says where it belongs
            self.err(f"{where}: unknown field '{field}' (a stage's fields are in .pi/skills/agent/references/plan-schema.md)")
        if (not st.instructions and kind in ("judgment", "deterministic", "side_effect") and st.pattern not in ("a2a", "approval")
                and not commanded(st) and st.agent != "batcher"):
            self.warn(f"{where}: a model child does it: give it `instructions` (what to do and how, in full), not only a title")

    def stage_a2a(self, where, st):
        """pattern: a2a and its `a2a:` block: another agent called over A2A, on the batcher (no model)."""
        if st.pattern != "a2a" and st.a2a is None:
            return
        if st.a2a is None:
            return self.err(f"{where}: pattern a2a needs `a2a:` (url, files, params, approve, token): the agent it calls")
        if st.pattern != "a2a":
            return self.err(f"{where}: `a2a:` is for a stage with pattern: a2a (not {st.pattern})")
        call = st.a2a
        if st.agent not in (None, "batcher"):
            self.err(f"{where}: an a2a stage runs on batcher (it sends a message and waits: no model), not {st.agent}")
        if st.program is not None:
            self.err(f"{where}: an a2a stage has no program: the agent it calls does the work")
        if st.kind.value == "decision":
            self.err(f"{where}: an a2a stage is deterministic, judgment or side_effect: the agent it calls can't ask anyone")
        self.check_refs(f"{where} a2a url", call.url)
        for name, value in call.params.items():
            if isinstance(value, str):
                self.check_refs(f"{where} a2a params.{name}", value)
        for remote, cid in call.files.items():
            if cid not in st.reads:
                self.err(f"{where}: a2a files sends '{cid}' as {remote}, but the stage doesn't read it; add it to reads")
        if call.token is not None:
            m = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", call.token.strip())
            spec = self.plan.params.get(m.group(1)) if m else None
            if spec is None or spec.type != "secret":
                self.err(f"{where}: a2a token is a secret param, written \"${{name}}\" (type: secret), never a value")
            else:
                self.check_refs(f"{where} a2a token", call.token, secret_ok=True)
        if call.approve and st.kind.value != "side_effect":
            self.err(f"{where}: it approves the called agent's side effects ({', '.join(call.approve)}): make its kind side_effect, "
                     "so the run asks before it")
        if not any(o.path for o in st.writes):
            self.err(f"{where}: an a2a stage downloads the called agent's outputs: give writes a path (a folder)")

    def stage_provisional(self, where, st):
        if not st.intent:
            self.err(f"{where}: provisional stages need an intent (what the stage must produce)")
        until = st.resolves_after
        if until not in self.by_key:
            self.err(f"{where}: resolves_after names unknown stage '{until}'")
        elif until in self.provisional:
            self.err(f"{where}: resolves_after '{until}', which is itself provisional")
        if st.resumes is not None:
            self.err(f"{where}: a provisional stage cannot resume a child; decide that at resolve")
        if st.gate:
            self.warn(f"{where}: gate on a provisional stage; it will be re-decided at resolve")

    def stage_agent(self, where, key, st, run):
        """agent / resumes / context rules; returns the agent the child runs as."""
        agent, resumes = st.agent, st.resumes
        if resumes is not None:
            if agent:
                self.err(f"{where}: resumes '{resumes}' and agent are mutually exclusive; a resumed child keeps its stored agent")
            if not run:
                self.err(f"{where}: a decision or approval stage cannot resume a child")
            target = self.by_key.get(resumes)
            if target is None:
                self.err(f"{where}: resumes unknown stage '{resumes}'")
            else:
                if not self.cyclic and resumes not in self.ancestors.get(key, set()):
                    self.err(f"{where}: resumes '{resumes}' but does not run after it")
                if resumes in self.provisional:
                    self.err(f"{where}: resumes provisional stage '{resumes}'")
                if not is_run_stage(target):
                    self.err(f"{where}: resumes '{resumes}', which launches no child")
                if target.pattern in MULTI_CHILD:
                    self.err(f"{where}: resumes '{resumes}', a {target.pattern} stage with many children; resume a single-child stage")
                if target.agent in self.external:
                    self.err(f"{where}: resumes '{resumes}', which runs on external runner '{target.agent}'; use a fresh stage that reads its output instead")
            if st.context is not None:
                self.warn(f"{where}: context is ignored on a resumed child")
            if st.model is not None:
                self.err(f"{where}: a resumed child keeps its model; remove model")
        elif run and not agent:
            self.err(f"{where}: agent is required for {st.kind.value} stages")
        if agent:
            self.check_agent_ref(where, agent)
        eff_agent = agent or (self.by_key[resumes].agent if resumes in self.by_key else None)
        if st.model is not None and eff_agent in self.external:
            self.warn(f"{where}: external runner '{eff_agent}' runs no model; model is ignored")
        if st.context is not None:
            if eff_agent in self.external and st.context == "fork":
                self.err(f"{where}: external runner '{eff_agent}' cannot use forked context")
            elif agent and self.default_ctx.get(agent) == st.context:
                self.warn(f"{where}: context '{st.context}' is already the default for '{agent}'; omit it")
        return eff_agent

    def stage_outputs(self, where, key, st, run, eff_agent, is_external):
        """Returned results, and how a judgment stage's outputs are checked; returns the
        returned outputs that have a schema."""
        returned = [o for o in st.writes if not o.path]
        if run and len(returned) > 1:
            self.err(f"{where}: {len(returned)} writes without a path; a child returns one result, so give the others a path")
        structured = [o for o in returned if o.schema_]
        if st.kind.value == "judgment":
            if not st.kind.reason:
                self.warn(f"{where}: judgment stage has no kind.reason; say why a program cannot decide correctness")
            has_review = bool(st.review) or key in self.reviewed
            for o in st.writes:
                if o.schema_:
                    continue
                if str(o.format or "").lower() in DOC_FORMATS:
                    if not has_review and st.pattern != "reviewer":
                        self.err(f"{where}: document output '{o.id}' has no schema, so it needs review or a downstream reviewer stage")
                else:
                    self.err(f"{where}: judgment output '{o.id}' needs a schema (or a document format such as md, with a reviewer)")
            if not (st.gate or st.review or st.done_when) and key not in self.reviewed and st.pattern != "reviewer":
                self.warn(f"{where}: judgment stage has no gate, review or done_when and no downstream reviewer")
        if structured and is_external:
            self.warn(f"{where}: external runner '{eff_agent}' has no outputSchema; the schema will be requested as JSON text and not enforced")
        return structured

    def stage_gate(self, where, st, run, structured, eff_agent, is_external):
        gate = st.gate
        if st.kind.value == "deterministic" and not (st.gate or st.review or st.done_when):
            self.err(f"{where}: deterministic stages need a gate, review or done_when")
        if st.review:
            self.check_agent_ref(f"{where} review", st.review)
        if gate is None:
            return
        typed = isinstance(gate, dict)
        command = gate.get("command") if typed else gate
        if typed:
            if not isinstance(command, str) or not command.strip():
                self.err(f"{where}: typed gate needs a command")
            if gate.get("output") != "json":
                self.err(f"{where}: typed gate needs output: json (use a plain string for a pass/fail command)")
            if structured:
                self.err(f"{where}: typed gate cannot be combined with a returned schema (outputSchema); give writes '{structured[0].id}' a path, or use a plain string gate")
        elif not isinstance(gate, str) or not gate.strip():
            self.err(f"{where}: gate must be a command string or a typed gate mapping")
        self.check_refs(f"{where} gate", command, secret_ok=commanded(st) or st.pattern == "a2a")
        if isinstance(command, str):
            self.gate_scripts.extend((where, m, "uv run" in command) for m in GATES_REF_RE.findall(command))
        if literal_absolute(command):
            self.warn(f"{where}: gate command has an absolute path; use a ${{param}} or an agent-relative path")
        if st.resumes is not None:
            self.err(f"{where}: gates are rejected on resumed children; verify in a following stage")
        if is_external:
            self.warn(f"{where}: external runner '{eff_agent}' does not support acceptance gates; run the check as its own script stage")
        if not run:
            self.err(f"{where}: a stage that launches no child cannot have a gate")

    def stage_map(self, where, st):
        pattern, spec = st.pattern, st.map or Map()
        over = spec.over
        if not over:
            self.err(f"{where}: {pattern} stages need `map.over`: the input or output it reads whose items are split into batches")
        elif over not in st.reads:
            self.err(f"{where}: map.over '{over}' must also be listed in reads")
        elif over in self.producer and not self.by_key[self.producer[over]].resolves_after:
            src = next((o for o in self.by_key[self.producer[over]].writes if o.id == over), None)
            if not (src and src.path):
                self.err(f"{where}: map.over '{over}' must be a file output (with a path) so it can be split")
        if st.kind.value == "deterministic":
            self.warn(f"{where}: deterministic work in a {pattern}; one script stage is usually better")
        b, max_batch = spec.batch, self.plan.limits.max_batch
        if b is None or b <= 0:
            self.err(f"{where}: {pattern} stages need a positive integer map.batch")
        elif max_batch is not None and b > max_batch:
            self.err(f"{where}: map.batch {b} exceeds limits.max_batch {max_batch}")

    # --- the rest of the plan ---------------------------------------------------------
    def outside_readers(self):
        """A judgment stage reading outside content (an input folder, file, url or app: emails, documents, pages) with
        an agent that has a shell: an instruction hidden in that content could run commands, reach the network or read
        the environment. `reader` (the template's) has no shell."""
        outside = {i.id for i in self.plan.inputs if i.kind in ("folder", "file", "url", "app")}
        defined = project_subagents(self.agent_root) if self.agent_root else {}
        for key, st in self.by_key.items():
            read = [c for c in st.reads if c in outside]
            if st.kind.value != "judgment" or not read or not st.agent or st.agent in self.external:
                continue
            tools = agent_tools(defined[st.agent]) if st.agent in defined else None
            has_shell = st.agent in SHELL_BUILTINS if tools is None else (not tools or "bash" in tools)
            if has_shell:
                self.warn(f"stage '{key}': reads outside content ({', '.join(read)}) as '{st.agent}', which has a shell: "
                          "an instruction hidden in it could run commands; use the subagent `reader` (no shell)")

    def side_effect_reach(self):
        """A side effect that writes outside the agent (an `app` or `message` output) needs an agent
        that can reach it: a connector (`mcp:` tools) or, for a CLI, bash. Builtin agents get neither
        extension nor MCP tools (a strict allowlist), so they can reach it only with bash."""
        outside = {o.id: o for o in self.plan.outputs if o.kind in ("app", "message")}
        defined = project_subagents(self.agent_root) if self.agent_root else {}
        for key, st in self.by_key.items():
            targets = [outside[o.id].path for o in st.writes if o.id in outside]
            if st.kind.value != "side_effect" or not targets or not st.agent:
                continue
            where = f"stage '{key}'"
            if st.agent in defined:
                tools = agent_tools(defined[st.agent])
                if tools and not any(x.startswith("mcp:") for x in tools) and "bash" not in tools:
                    self.err(f"{where}: agent '{st.agent}' cannot reach {', '.join(targets)}: its tools list no connector "
                             "(mcp:<server> or mcp:<server>/<tool>) and no bash")
            elif self.agent_sources.get(st.agent, "builtin") == "builtin":
                self.warn(f"{where}: builtin agent '{st.agent}' writes {', '.join(targets)} but has no connector tools "
                          "(builtin agents get no extension or MCP tools), so it can reach it only with a CLI through bash. "
                          "For a connector, use a subagent in .pi/agents/ whose tools list it (mcp:<server>)")

    def outputs(self):
        for out in self.plan.outputs:
            where = f"output '{out.id}'"
            self.check_refs(where, out.path, is_path=out.kind == "file")
            if out.kind == "file" and literal_absolute(out.path):
                self.warn(f"{where}: absolute path; use a ${{param}} so the agent can be moved")
            if out.id not in self.producer:
                self.err(f"{where}: no stage produces it"); continue
            pkey = self.producer[out.id]
            if (out.kind in ("app", "message") or out.overwrite) and self.by_key[pkey].kind.value != "side_effect":
                self.err(f"{where}: {out.kind}{' overwrite' if out.overwrite else ''} output must come from a side_effect stage (produced by '{pkey}')")

    def run_and_waves(self):
        """Stage status (from the run) and the waves derived from it."""
        err, by_key = self.err, self.by_key
        if "waves" in self.raw:
            err("plans do not list waves: they are derived from the stages (remove `waves`; give a stage "
                "`pauses_after: true` if the user should review it before the stages that need it run)")
        for key, st in by_key.items():
            if "status" in (st.model_extra or {}):
                err(f"stage '{key}': status belongs to the run (runs/<id>/run.yaml), not the plan; remove it")
        run_id, rs = None, self.run_state
        if rs is None and self.agent_root:
            if run := active_run(self.agent_root):
                run_id, rs = run.id, run.state
        elif rs is not None:
            run_id = rs.get("id")
        self.run_id, self.rs = run_id, rs or {}
        stage_status = {k: v for k, v in (self.rs.get("stages") or {}).items() if v in ("running", "done")}
        for key in stage_status:
            if key not in by_key:
                self.warn(f"run {run_id}: stage '{key}' ran but is no longer in the plan")
        stage_status = {k: v for k, v in stage_status.items() if k in by_key}
        for key, sst in stage_status.items():
            if key in self.provisional:
                err(f"stage '{key}' is provisional but {sst} in run {run_id}; resolve it before it runs")
            allowed = ("done",) if sst == "done" else ("done", "running")
            for d in self.after[key]:
                if stage_status.get(d, "pending") not in allowed:
                    err(f"stage '{key}' is {sst} in run {run_id} but runs after '{d}', which is {stage_status.get(d, 'pending')}")
        self.stage_status = stage_status

        # wave ids continue from the waves this run has finished
        first = 1 + sum(1 for r in self.rs.get("launches") or [] if isinstance(r, dict) and r.get("status") == "done")
        self.waves, level = [], {}
        if not self.cyclic:
            try:
                self.waves, level = derive_waves(by_key, self.after, stage_status, first)
            except CycleError as exc:
                err("resolves_after makes a cycle: " + " -> ".join(reversed(exc.args[1]))
                    + "; a provisional stage comes after the discovery it waits for")
        self.running = [k for k, v in stage_status.items() if v == "running"]
        self.next_wave = self.waves[0] if self.waves and not self.running else None

        # A child's returned result only exists inside the agent run that
        # produced it; later waves can only read files.
        for key, st in by_key.items():
            for cid in st.reads if stage_status.get(key) != "done" else []:
                pkey = self.producer.get(cid)
                if pkey is None or pkey in self.provisional or not is_run_stage(by_key[pkey]):
                    continue
                o = next((o for o in by_key[pkey].writes if o.id == cid), None)
                if o and not o.path and (stage_status.get(pkey) == "done" or level.get(pkey, 0) < level.get(key, 0)):
                    err(f"stage '{key}' reads '{cid}', a returned result of stage '{pkey}' from an earlier wave; give that output a path so it survives the wave")

        if self.next_wave is not None:
            prov_next = [k for k in self.next_wave["stages"] if k in self.provisional]
            if prov_next and self.plan.status == "ready":
                err(f"the next wave to run, '{self.next_wave['id']}', has provisional stages {prov_next}; resolve before marking the plan ready")

    def unknowns(self):
        err, plan = self.err, self.plan
        self.unresolved_unknowns = []
        q_ids = {q.id for q in plan.questions}
        for uid in duplicates(u.id for u in plan.unknowns):
            err(f"unknown '{uid}': duplicate id")
        for u in plan.unknowns:
            where, rb = f"unknown '{u.id}'", u.resolved_by
            if rb in ("planner", "probe"):
                if not u.finding:
                    self.unresolved_unknowns.append(u.id)
                    if plan.status in ("needs_answers", "ready"):
                        err(f"{where}: resolved_by {rb} but no finding; resolve it before asking or finishing")
                if rb == "probe" and u.finding and not u.run_id:
                    self.warn(f"{where}: probe finding has no run_id")
            elif rb == "user":
                if u.question not in q_ids:
                    err(f"{where}: resolved_by user needs `question` naming an existing question")
            else:  # discovery
                sk = u.stage
                if sk not in self.by_key:
                    err(f"{where}: resolved_by discovery needs `stage` naming an existing stage")
                elif sk in self.provisional:
                    err(f"{where}: discovery stage '{sk}' is provisional")
                elif not u.finding and not any(self.by_key[p].resolves_after == sk for p in self.provisional):
                    err(f"{where}: no stage has resolves_after '{sk}', so nothing resolves on this discovery")
                for a in u.affects:
                    if a not in self.by_key:
                        err(f"{where}: affects unknown stage '{a}'")

    def assumptions(self):
        for aid in duplicates(a.id for a in self.plan.assumptions):
            self.err(f"assumption '{aid}': duplicate id")
        confirm_count = sum(a.confirm for a in self.plan.assumptions)
        if confirm_count > 5:
            self.warn(f"{confirm_count} assumptions need confirmation; consider asking instead")

    def critique(self):
        err, plan, c = self.err, self.plan, self.plan.critique
        self.critic_required = len(self.by_key) > 3 or any(st.kind.value in ("side_effect", "judgment") for st in self.by_key.values())
        self.finding_ids = {f.id for f in c.findings} if c else set()
        self.asked_findings = [f.id for f in c.findings if f.disposition == "asked"] if c else []
        self.verdict = c.verdict if c else None
        if c:
            self.check_agent_ref("critique", c.agent)
            crev = plan.revision if c.revision is None else c.revision
            if crev != plan.revision:
                self.warn(f"critique is for revision {crev} but the plan is at revision {plan.revision}; re-run the critic after resolving")
            if plan.status == "ready" and (why := check_ready(self.raw)):
                err(why)
            for fid in duplicates(f.id for f in c.findings):
                err(f"critic finding '{fid}': duplicate id")
            for f in c.findings:
                if f.disposition in ("accepted", "rejected") and not f.note:
                    err(f"critic finding '{f.id}': {f.disposition} findings need a note saying what changed or why not")
                if f.disposition == "rejected" and f.severity == "blocking":
                    self.warn(f"critic finding '{f.id}' is blocking and was rejected; tell the user why")
            if self.verdict in ("revise", "ask_user") and not c.findings:
                err(f"critique.verdict is {self.verdict} but no findings are recorded")
        if self.critic_required and plan.status in ("needs_answers", "ready") and c is None:
            err(f"status is {plan.status} but the plan has no critique; it has side-effect, judgment or more than 3 stages, so run the plan critic first")

    def questions(self):
        err, plan = self.err, self.plan
        for qid in duplicates(q.id for q in plan.questions):
            err(f"question '{qid}': duplicate id")
        self.open_q, covered_findings = [], set()
        for q in plan.questions:
            where = f"question '{q.id}'"
            for b in q.blocks:
                if b not in self.by_key:
                    err(f"{where}: blocks unknown stage '{b}'")
            is_open = q.answer is None or (isinstance(q.answer, str) and not q.answer.strip())
            if is_open:
                self.open_q.append(q.id)
            if q.from_finding is not None:
                # an answered question may come from an earlier revision's critique (kept in the agent's git history)
                if is_open and q.from_finding not in self.finding_ids:
                    err(f"{where}: from_finding '{q.from_finding}' is not a recorded critic finding")
                covered_findings.add(q.from_finding)
        for fid in self.asked_findings:
            if fid not in covered_findings:
                err(f"critic finding '{fid}' is marked asked but no question has from_finding: {fid}")
        if plan.status == "ready" and self.open_q:
            err(f"status is ready but questions {self.open_q} are unanswered; answer them or convert them to assumptions")
        if plan.status == "needs_answers" and not self.open_q:
            err("status is needs_answers but no question is open")
        if len(self.open_q) > 3:
            self.warn(f"{len(self.open_q)} open questions; ask at most 3 per round")

    def params(self):
        """The params and every ${name} the plan uses."""
        err, params = self.err, self.plan.params
        env_names = {}
        for name, spec in params.items():
            env = agent_params.env_name(name, spec)
            if not agent_params.ENV_NAME_RE.match(env):
                err(f"param '{name}': '{env}' is not a valid environment variable name; set env: to one (letters, digits, _)")
            elif agent_params.reserved(env):
                err(f"param '{name}': reads ${env}, which the system or a tool already uses; set env: to a name of its own")
            if env in env_names:
                err(f"param '{name}': reads ${env}, like param '{env_names[env]}'")
            env_names.setdefault(env, name)
            if spec.type == "secret" and spec.default not in (None, ""):
                err(f"param '{name}': a secret has no default; its value stays in the environment or .env")
            if spec.type == "secret" and spec.per_run:
                err(f"param '{name}': a secret can't be per_run; nobody gives its value in a run, it stays in the environment or .env")
        for where, ref, is_path, embedded, secret_ok in self.param_refs:
            if ref not in params:
                err(f"{where}: references undefined param '${{{ref}}}'")
            elif is_path and params[ref].type == "secret":
                err(f"{where}: a secret (param '{ref}') cannot be part of a path")
            elif params[ref].type == "secret" and not secret_ok:
                # no model child gets a secret: they share one sandbox, and a model reading outside content can be
                # told to send it somewhere
                err(f"{where}: secret '{ref}' reaches only what the batcher runs: a stage program's args and its gate, "
                    "or an a2a stage's token; give the stage a program with args, or reach the service through a connector")
            elif embedded and params[ref].type == "path":
                err(f"{where}: '${{{ref}}}' compiles to an absolute path; start the path with it (`${{{ref}}}/…`, not `../${{{ref}}}`)")
        used = {ref for _, ref, *_ in self.param_refs}
        for name, spec in params.items():
            if name not in used and spec.type != "secret":  # a secret may be read only from the environment (tools, scripts)
                self.warn(f"param '{name}' is declared but never used")

    def agent_folder(self):
        """Checks against the agent folder: its files, gate scripts, agents and param values."""
        self.agent_info = None
        root = self.agent_root
        if not root:
            return
        err, params, status = self.err, self.plan.params, self.plan.status
        info = self.agent_info = {"root": str(root.resolve())}
        for rel, what in (
            ("README.md", "the folder's guide for people"),
            (".pi/APPEND_SYSTEM.md", "the agent's role"),
            (".pi/extensions/agent/index.ts", "the /agent-* commands and agent_* tools"),
            (".pi/skills/agent/SKILL.md", "the planning skill used at resolve"),
            (".pi/settings.json", "project package declarations"),
            (".pi/agents/batcher.md", "the command runner map stages use"),
            (".pi/skills/agent/scripts/run_compile.py", "the wave compiler"),
        ):
            if not (root / rel).exists():
                self.warn(f"agent: {rel} is missing ({what}); re-run agent_scaffold.py --refresh")
        packages = project_packages(root)
        for where, script, with_uv in self.gate_scripts:
            if not (root / "gates" / script).exists():
                (err if status == "ready" else self.warn)(
                    f"{where}: gate script gates/{script} does not exist; write it while planning (never in the stage it checks)")
            elif script.endswith(".py") and (problem := package_problem(root / "gates" / script, packages, with_uv)):
                err(f"{where}: gates/{script} would fail before checking anything: it {problem}")
        for program, key in self.script_owner.items():  # stage programs run with uv run
            path = root / "scripts" / program
            if path.suffix == ".py" and path.is_file() and (problem := package_problem(path, packages, True)):
                err(f"stage '{key}': scripts/{program} {problem}")
        on_disk = project_agent_names(root)
        for name, src in self.agent_sources.items():
            if src == "project" and name not in on_disk:
                err(f"agent: agent '{name}' has source project but no .pi/agents/*.md defines it")
            elif src == "builtin" and name in on_disk:
                self.warn(f"agent: agent '{name}' is defined in .pi/agents/ but listed as builtin; list it as {{name: {name}, source: project}}")
        valid = {n: s for n, s in params.items() if agent_params.ENV_NAME_RE.match(agent_params.env_name(n, s))}
        values = agent_params.load(root, valid)
        unset = agent_params.missing(valid, values)
        info["missing_params"] = [n for n in unset if params[n].type != "secret"]
        info["missing_secrets"] = [agent_params.env_name(n, params[n]) for n in unset if params[n].type == "secret"]
        # Input files and folders live outside the agent; children run in work/, so a
        # relative path would silently point inside the agent.
        for inp in self.plan.inputs:
            loc = inp.path or ""
            refs = PARAM_REF_RE.findall(loc)
            if inp.kind not in ("folder", "file") or not refs or any(values.get(r) is None or r not in valid for r in refs):
                continue  # no param, or not set yet (reported as missing)
            path = PARAM_REF_RE.sub(lambda m: str(values[m.group(1)]), loc)
            full = Path(path).expanduser()
            if not full.is_absolute():
                err(f"agent: input '{inp.id}' resolves to '{path}', a relative path; set an absolute path in .env (~/… is fine)")
            elif not full.exists():
                err(f"agent: input '{inp.id}' resolves to '{path}', which does not exist; check the value in .env")

    def summary(self):
        summary = {
            "revision": self.plan.revision,
            "stages": len(self.by_key),
            "run": {"id": self.run_id, "status": self.rs.get("status")} if self.run_id else None,
            "last_run": last_run(self.agent_root) if self.agent_root else None,
            "done": [k for k, v in self.stage_status.items() if v == "done"],
            "running": self.running,
            "provisional": sorted(self.provisional),
            "next_wave": self.next_wave["id"] if self.next_wave else None,
            "waves": self.waves,
            "open_questions": len(self.open_q),
            "unresolved_unknowns": self.unresolved_unknowns,
            "approvals": self.approvals,
            "agent_keys": self.keys_summary,
            "agents_checked": self.agent_names is not None,
            "critic_required": self.critic_required,
            "critic_verdict": self.verdict,
        }
        if self.agent_info is not None:
            summary["agent"] = self.agent_info
        return summary


def main(
    plan_path: Annotated[str, typer.Argument(metavar="PLAN.yaml")],
    agents: Annotated[str | None, typer.Option(help="comma-separated agent names from subagent list")] = None,
    external: Annotated[str | None, typer.Option(help="comma-separated names of agents on external runners")] = None,
    agent_dir: Annotated[str | None, typer.Option("--agent", help="agent root (default: detected from <agent>/plan.yaml)")] = None,
):
    """Validate an agent YAML file."""
    plan_path = Path(plan_path)
    try:  # the whole plan: plan.yaml and .plan/ beside it (another file name: that file alone)
        plan = (read_plan(plan_path.resolve().parent, strict=True) if plan_path.name == PLAN_FILE
                else load_yaml(plan_path.read_text(encoding="utf-8")))
    except (OSError, YAMLError) as exc:
        print(json.dumps({"valid": False, "errors": [f"cannot read plan: {exc}"], "warnings": []}))
        sys.exit(2)
    agent_root = Path(agent_dir) if agent_dir is not None else None
    if agent_root is None:
        parent = plan_path.resolve().parent
        if (parent / ".pi").is_dir():
            agent_root = parent
    errors, warnings, summary = validate(plan, agents, external, agent_root)
    # plan.yaml is for people: how it was planned has its own files
    if plan_path.name == PLAN_FILE:
        errors += [f"{part}: belongs in .plan/{part}.yaml, not plan.yaml (plan.yaml is for people to read)"
                   for part in parts_in_plan_file(plan_path.resolve().parent)]
    where = None
    if agent_root is not None:
        from agent_state import read_next  # imported late: it imports this module
        where = read_next(agent_root)
    print(json.dumps({
        "valid": not errors,
        "status": plan.get("status") if isinstance(plan, dict) else None,
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
        "state": where,
    }, indent=2))
    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    typer.run(main)
