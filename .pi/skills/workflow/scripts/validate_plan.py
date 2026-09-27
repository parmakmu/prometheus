#!/usr/bin/env python3
"""Validate a workflow YAML file against the plan schema, the
pi-subagents constraints it will be compiled into, and (when the plan lives in
a workflow) the workflow's portability.

Usage:
  validate_plan.py PLAN.yaml [--agents a,b,c] [--external x,y] [--workflow DIR]

In a workflow, stage status comes from the active run (runs/<id>/run.yaml; see
"Runs" below): the plan is reusable, each run is one execution of it.

--agents    agent names from subagent({ action: "list" }); overrides the
            plan's `agents.available` list
--external  names among them whose profile uses an external runner
--workflow  workflow root; defaults to the folder above `.pi/` when
            the plan is at <workflow>/.pi/plan.yaml

Two passes: the pydantic models below check the plan's shape (types, required
fields, allowed values); the rules in validate() check how the parts fit
together, on a plan whose shape is known to be right.

Prints one JSON object {valid, status, errors, warnings, summary} and exits
0 when there are no errors, 1 otherwise (2 on usage or read errors).
Requires typer, pydantic and ruamel.yaml (`uv sync`).
"""
import json
import os
import re
import sys
from graphlib import CycleError, TopologicalSorter
from typing import Annotated, Any, Literal

import typer
import workflow_params
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


def rt_yaml():
    """Round-trip YAML in the plans' house style (quotes kept, no line wrapping,
    lists as `  - key:`), so a load + dump changes only what was edited. It rejects
    repeated keys: a plain loader keeps the last one silently, so an edit that
    drops a `- id:` line would merge two list items."""
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def load_yaml(src):
    """Parse YAML text or an open file (round-trip, see rt_yaml)."""
    return rt_yaml().load(src)


# --- runs ---------------------------------------------------------------------
# Each execution of a workflow's plan is a folder runs/<id>/ (id: <date>-<n>):
#   run.yaml    id, status (active | done | stopped), revision, started_at,
#               finished_at, note, stages {key: running | done} (absent = pending),
#               launches [one entry per wave launch]; written by run_log.py
#   work/       the children's cwd: stage outputs, decisions, batches, scripts
#   .compiled/  compiled waves and their results
# Relative param values (an `outputs` default, say) resolve against runs/<id>/.
# At most one run is active; /workflow-run continues it.
RUNS = "runs"
RUN_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(\d+)$")


def list_runs(workflow_root):
    """[(id, dir)] of the workflow's runs, oldest first."""
    base = os.path.join(workflow_root, RUNS)
    names = [n for n in os.listdir(base) if os.path.isfile(os.path.join(base, n, "run.yaml"))] if os.path.isdir(base) else []
    key = lambda n: (m.group(1), int(m.group(2))) if (m := RUN_ID_RE.match(n)) else (n, 0)
    return [(n, os.path.join(base, n)) for n in sorted(names, key=key)]


def read_run(run_dir):
    with open(os.path.join(run_dir, "run.yaml"), encoding="utf-8") as fh:
        state = load_yaml(fh)
    return state if isinstance(state, dict) else {}


def last_run(workflow_root):
    """The latest run in brief (so the parent need not look in runs/), or None."""
    runs = list_runs(workflow_root)
    if not runs:
        return None
    rid, d = runs[-1]
    try:
        state = read_run(d)
    except (OSError, YAMLError):
        state = {}
    launches = state.get("launches") or []
    last = launches[-1] if launches else {}
    return {"id": rid, "status": state.get("status"), "started_at": state.get("started_at"),
            "finished_at": state.get("finished_at"), "note": state.get("note"), "runs": len(runs),
            "last_wave": {"wave": last.get("wave"), "status": last.get("status")} if last else None}


def active_run(workflow_root):
    """(id, dir, state) of the run /workflow-run continues, or None."""
    for rid, d in reversed(list_runs(workflow_root)):
        try:
            state = read_run(d)
        except (OSError, YAMLError):
            continue
        if state.get("status") == "active":
            return rid, d, state
    return None


MULTI_CHILD = {"map", "map_reduce", "lanes", "council"}
EXTERNAL_RUNNERS = {"external-cli", "external-job"}
DOC_FORMATS = {"md", "markdown", "txt", "docx", "html", "pdf"}
GATES_REF_RE = re.compile(r"\.\./gates/([^\s'\"]+)")
PARAM_REF_RE = re.compile(r"\$\{([^}]*)\}")
SCRIPT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
ABS_PATH_RE = re.compile(r"(^|[\s'\"=])(/[^\s'\"]+|~[^\s'\"]*|[A-Za-z]:[\\/][^\s'\"]*)")

# Launch context pi-subagents' builtin agents use when `context` is omitted.
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
    type: Literal["path", "text", "secret"] = "path"  # see workflow_params.py
    env: Str | None = None  # the environment variable; default: the name in upper case
    default: Any = None
    required: StrictBool = True


class Input(M):
    id: Str
    kind: Literal["folder", "file", "app", "url", "text"]
    location: str | None = None
    access: Literal["read", "read_write"] = "read"


class Output(M):
    id: Str
    kind: Literal["file", "app", "message"]
    location: Str
    overwrite: StrictBool = False


class AgentEntry(M):
    name: Str
    source: Literal["builtin", "package", "project", "user"] = "builtin"
    runner: Literal["native", "external-cli", "external-job"] | None = None
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


class Verify(M):
    gate: Any = None  # a command string or {command, output: json}; checked in validate()
    review: str | None = None
    check: str | None = None


class Stage(M):
    key: Key
    kind: Literal["deterministic", "judgment", "decision", "side_effect"]
    title: str | None = None
    kind_reason: str | None = None
    pattern: Literal["script", "map", "map_reduce", "scout", "worker", "reviewer", "lanes", "council", "approval"] | None = None
    agent: str | None = None
    resumes: str | None = None
    context: Context | None = None
    provisional: StrictBool = False
    resolves_after: str | None = None
    intent: str | None = None
    needs: list[str] = []
    consumes: list[str] = []
    out: list[StageOut] = []
    verify: Verify | None = None
    approval: Literal["none", "required"] = "none"
    pause_after: StrictBool = False
    items: str | None = None
    items_glob: str | None = None
    batch: int | None = None
    script: str | None = None  # the stage's program, kept in the workflow's scripts/ and reused by later runs


class Unknown(M):
    id: Str
    text: Str
    resolve_by: Literal["planner", "probe", "user", "discovery"]
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


class Plan(M):
    version: Literal[1]
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
                    name = entry.get("key", entry.get("id")) if isinstance(entry, dict) else None
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
    return st.kind != "decision" and st.pattern != "approval"


def human_first(st):
    """Stages the parent handles with the user before a wave's script starts."""
    return st.kind in ("decision", "side_effect") or st.pattern == "approval"


def derive_waves(by_key, needs, status, first=1):
    """Group the stages that are not done into waves, each stage as early as its needs allow.

    A workflow script cannot pause, so a wave ends wherever the parent must act
    between two stages. A stage goes in a later wave than a stage it needs when
    - it is a decision, approval or side effect and the needed stage launches a
      child (the parent asks the user before the wave's script starts);
    - the needed stage is a side effect (its results are reported first) or has
      `pause_after` (the user reviews it first);
    - it is provisional and the needed stage is the discovery it `resolves_after`
      (the parent replans in between; this counts as a need).
    `status` maps keys to running | done (absent = pending).
    Returns ([{id, stages, ends_with}], {key: wave index}); raises CycleError."""
    active = [k for k in by_key if status.get(k) != "done"]
    deps = {k: {d for d in needs[k] if d in active} for k in active}
    for k in active:
        st = by_key[k]
        if st.provisional and st.resolves_after in deps:
            deps[k].add(st.resolves_after)
    level = {}
    for k in TopologicalSorter(deps).static_order():
        st = by_key[k]
        level[k] = max((level[d] + int(bool(
            by_key[d].kind == "side_effect" or by_key[d].pause_after
            or (human_first(st) and is_run_stage(by_key[d]))
            or (st.provisional and d == st.resolves_after)))
            for d in deps[k]), default=0)
    waves = []
    for i in range(max(level.values(), default=-1) + 1):
        members = [k for k in active if level[k] == i]  # plan order
        ends_with = "none"
        if any(by_key[k].kind == "side_effect" for k in members):
            ends_with = "approval"
        if any(by_key[k].pause_after for k in members):
            ends_with = "review"
        if any(by_key[p].provisional and by_key[p].resolves_after in members for p in active):
            ends_with = "replan"
        waves.append({"id": f"w{first + i}", "stages": members, "ends_with": ends_with})
    return waves, level


def child_keys(key, pattern):
    if pattern == "map":
        return f"{key}-split, {key}-<n>, {key}-merge"
    if pattern == "map_reduce":
        return f"{key}-split, {key}-<n>, {key}-reduce"
    if pattern in ("lanes", "council"):
        return f"{key}-<name>"
    return key


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


def read_frontmatter_name(path):
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    end = text.find("\n---", 3) if text.startswith("---") else -1
    if end == -1:
        return None
    try:
        meta = YAML(typ="safe").load(text[3:end]) or {}
    except YAMLError:
        return None
    return meta.get("name") if isinstance(meta, dict) else None


def project_agent_names(root):
    names = set()
    for dirpath, _, files in os.walk(os.path.join(root, ".pi", "agents")):
        for f in files:
            if f.endswith(".md"):
                names.add(read_frontmatter_name(os.path.join(dirpath, f)) or f[:-3])
    return names


def load_agents(plan, cli_agents, cli_external, warn):
    """Return (names or None, external set, default-context map, source map)."""
    entries = plan.agents.available if plan.agents else []
    external = {a.strip() for a in (cli_external or "").split(",") if a.strip()}
    sources = {a.name: a.source for a in entries}
    contexts = BUILTIN_CONTEXT | {a.name: a.context for a in entries if a.context}
    if cli_agents is not None:
        return {a.strip() for a in cli_agents.split(",") if a.strip()} | external, external, contexts, sources
    external |= {a.name for a in entries if a.runner in EXTERNAL_RUNNERS}
    if plan.agents is None:
        warn("no agents block and no --agents: agent names were not checked against subagent list")
        return None, external, contexts, sources
    if not entries:
        warn("agents.available is empty: agent names were not checked")
        return None, external, contexts, sources
    return {a.name for a in entries}, external, contexts, sources


# --- rules --------------------------------------------------------------------

def validate(raw, cli_agents=None, cli_external=None, workflow_root=None, run_state=None):
    """`run_state`: the run whose stage status applies (a run.yaml mapping); in a workflow,
    default the active run. Without one, every stage is pending."""
    if not isinstance(raw, dict):
        return ["plan must be a YAML mapping"], [], {}
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as exc:
        return shape_errors(exc, raw), [], {}
    errors, warnings = [], []
    err, warn = errors.append, warnings.append
    status, revision, params = plan.status, plan.revision, plan.params

    for k in plan.limits.model_extra or {}:
        warn(f"limits.{k} is not a known limit")

    param_refs = []  # (where, name, is a path)

    def check_refs(where, text, is_path=False):
        if isinstance(text, str):
            param_refs.extend((where, ref, is_path) for ref in PARAM_REF_RE.findall(text))

    agent_names, external, default_ctx, agent_sources = load_agents(plan, cli_agents, cli_external, warn)

    def check_agent_ref(where, name):
        if agent_names is not None and name not in agent_names:
            err(f"{where}: agent '{name}' is not in the available agents")
        if agent_sources.get(name) == "user":
            err(f"{where}: agent '{name}' is user-scoped and will not exist where the workflow is copied; copy its file to .pi/agents/ and set source: project")

    # --- inputs ------------------------------------------------------------
    input_ids = {inp.id for inp in plan.inputs}
    for iid in duplicates(inp.id for inp in plan.inputs):
        err(f"input '{iid}': duplicate id")
    for inp in plan.inputs:
        where = f"input '{inp.id}'"
        if not inp.location and inp.kind != "text":
            err(f"{where}: location is required")
        check_refs(where, inp.location, is_path=inp.kind in ("folder", "file"))
        if inp.kind in ("folder", "file") and literal_absolute(inp.location):
            warn(f"{where}: absolute location; use a ${{param}} so the workflow can be moved")

    # --- stages: keys and outputs --------------------------------------------
    by_key, producer = {}, {}
    for st in plan.stages:
        if st.key in by_key:
            err(f"stage '{st.key}': duplicate key"); continue
        by_key[st.key] = st
        for o in st.out:
            if o.id in input_ids:
                err(f"stage '{st.key}': out id '{o.id}' reuses an input id")
            if o.id in producer:
                err(f"stage '{st.key}': out id '{o.id}' is already produced by stage '{producer[o.id]}'")
            else:
                producer[o.id] = st.key

    provisional = {k for k, st in by_key.items() if st.provisional}

    for key, st in by_key.items():
        reserved = {
            "map": rf"^{re.escape(key)}-(\d+|split|merge)$",
            "map_reduce": rf"^{re.escape(key)}-(\d+|split|reduce)$",
            "lanes": rf"^{re.escape(key)}-",
            "council": rf"^{re.escape(key)}-",
        }.get(st.pattern)
        for other in by_key:
            if reserved and other != key and re.match(reserved, other):
                err(f"stage '{other}' collides with child keys of {st.pattern} stage '{key}' ({child_keys(key, st.pattern)})")
        if st.verify and st.verify.review and f"{key}-review" in by_key:
            err(f"stage '{key}-review' collides with the review child the runner adds for stage '{key}' (verify.review)")

    # --- dependency graph ------------------------------------------------------
    needs = {}
    for key, st in by_key.items():
        for dep in st.needs:
            if dep == key:
                err(f"stage '{key}': needs itself")
            elif dep not in by_key:
                err(f"stage '{key}': needs unknown stage '{dep}'")
        needs[key] = [d for d in st.needs if d in by_key and d != key]

    ancestors, cyclic = {}, False
    try:
        for k in TopologicalSorter(needs).static_order():
            ancestors[k] = set(needs[k]).union(*(ancestors[d] for d in needs[k]))
    except CycleError as exc:  # args[1] runs dependency -> dependent; show "needs" order
        cyclic = True
        err("dependency cycle: " + " -> ".join(reversed(exc.args[1])))

    reviewed = set().union(*(ancestors.get(k, set()) for k, st in by_key.items() if st.pattern == "reviewer"))

    # --- stages: per-stage rules ------------------------------------------------
    approvals, keys_summary = [], {}
    gate_scripts = []  # (where, file under <workflow>/gates/)
    script_owner = {}  # stage program -> the stage it belongs to
    for key, st in by_key.items():
        where = f"stage '{key}'"
        kind, pattern, agent, resumes = st.kind, st.pattern, st.agent, st.resumes
        prov, run = key in provisional, is_run_stage(st)

        if pattern is None and not prov:
            err(f"{where}: pattern is required")
        if not st.title:
            warn(f"{where}: no title")
        if st.approval == "required":
            approvals.append(key)
        if kind == "side_effect" and st.approval != "required":
            err(f"{where}: side_effect stages need approval: required, provisional or not")

        # consumes (checked for every stage)
        for cid in st.consumes:
            if cid in input_ids:
                continue
            if cid not in producer:
                err(f"{where}: consumes unknown id '{cid}'")
            elif not cyclic and producer[cid] not in ancestors.get(key, set()):
                err(f"{where}: consumes '{cid}' from stage '{producer[cid]}', which it does not need (directly or transitively)")

        for o in st.out:
            if o.path:
                check_refs(f"{where} out '{o.id}'", o.path, is_path=True)
                if literal_absolute(o.path):
                    err(f"{where}: out '{o.id}' path is absolute; use a ${{param}} or a path relative to the workflow")

        if st.script is not None:
            if not SCRIPT_NAME_RE.match(st.script):
                err(f"{where}: script '{st.script}' must be a file name in the workflow's scripts/ (no folders)")
            if st.kind not in ("deterministic", "side_effect"):
                err(f"{where}: script is for deterministic and side-effect stages; a {st.kind} stage has no program")
            if st.pattern in MULTI_CHILD:
                err(f"{where}: script on a {st.pattern} stage is not supported yet: every batch child would write it")
            elif st.pattern != "script":
                err(f"{where}: a stage with a program has pattern: script (not {st.pattern})")
            if st.script in script_owner:
                err(f"{where}: script '{st.script}' is also stage '{script_owner[st.script]}'s; give each stage its own")
            script_owner.setdefault(st.script, st.key)
        elif st.kind == "deterministic" and st.pattern == "script":
            warn(f"{where}: name its program with `script:` (kept in scripts/, reviewed, reused by later runs); "
                 "without it every run writes the program again")

        if prov:
            keys_summary[key] = "(provisional)"
            if not st.intent:
                err(f"{where}: provisional stages need an intent (what the stage must produce)")
            ra = st.resolves_after
            if not ra:
                err(f"{where}: provisional stages need resolves_after (the discovery stage that will fill them in)")
            elif ra not in by_key:
                err(f"{where}: resolves_after unknown stage '{ra}'")
            elif ra in provisional:
                err(f"{where}: resolves_after '{ra}', which is itself provisional")
            if resumes is not None:
                err(f"{where}: a provisional stage cannot resume a child; decide that at replan")
            if st.verify and st.verify.gate:
                warn(f"{where}: gate on a provisional stage; it will be re-decided at replan")
            continue  # detail rules apply once the stage is concrete

        if run:
            keys_summary[key] = child_keys(key, pattern)
        if st.resolves_after is not None:
            err(f"{where}: resolves_after is only for provisional stages")

        # agent / resumes
        if resumes is not None:
            if agent:
                err(f"{where}: resumes '{resumes}' and agent are mutually exclusive; a resumed child keeps its stored agent")
            if not run:
                err(f"{where}: a decision or approval stage cannot resume a child")
            target = by_key.get(resumes)
            if target is None:
                err(f"{where}: resumes unknown stage '{resumes}'")
            else:
                if not cyclic and resumes not in ancestors.get(key, set()):
                    err(f"{where}: resumes '{resumes}' but does not need it")
                if resumes in provisional:
                    err(f"{where}: resumes provisional stage '{resumes}'")
                if not is_run_stage(target):
                    err(f"{where}: resumes '{resumes}', which launches no child")
                if target.pattern in MULTI_CHILD:
                    err(f"{where}: resumes '{resumes}', a {target.pattern} stage with many children; resume a single-child stage")
                if target.agent in external:
                    err(f"{where}: resumes '{resumes}', which runs on external runner '{target.agent}'; use a fresh stage that consumes its output instead")
            if st.context is not None:
                warn(f"{where}: context is ignored on a resumed child")
        elif run and not agent:
            err(f"{where}: agent is required for {kind} stages")

        if agent:
            check_agent_ref(where, agent)
        eff_agent = agent or (by_key[resumes].agent if resumes in by_key else None)
        is_external = eff_agent in external

        if st.context is not None:
            if is_external and st.context == "fork":
                err(f"{where}: external runner '{eff_agent}' cannot use forked context")
            elif agent and default_ctx.get(agent) == st.context:
                warn(f"{where}: context '{st.context}' is already the default for '{agent}'; omit it")

        returned = [o for o in st.out if not o.path]
        if run and len(returned) > 1:
            err(f"{where}: {len(returned)} out entries without path; a child returns one result, so give the others a path")
        structured = [o for o in returned if o.schema_]
        v = st.verify
        if kind == "judgment":
            if not st.kind_reason:
                warn(f"{where}: judgment stage has no kind_reason; say why a program cannot decide correctness")
            has_review = bool(v and v.review) or key in reviewed
            for o in st.out:
                if o.schema_:
                    continue
                if str(o.format or "").lower() in DOC_FORMATS:
                    if not has_review and pattern != "reviewer":
                        err(f"{where}: document output '{o.id}' has no schema, so it needs verify.review or a downstream reviewer stage")
                else:
                    err(f"{where}: judgment output '{o.id}' needs a schema (or a document format such as md, with a reviewer)")
            if not v and key not in reviewed and pattern != "reviewer":
                warn(f"{where}: judgment stage has no verify and no downstream reviewer")
        if structured and is_external:
            warn(f"{where}: external runner '{eff_agent}' has no outputSchema; the schema will be requested as JSON text and not enforced")

        gate = v.gate if v else None
        if kind == "deterministic" and not (v and (v.gate or v.review or v.check)):
            err(f"{where}: deterministic stages need verify with gate, review or check")
        if v and v.review:
            check_agent_ref(f"{where} verify.review", v.review)
        if gate is not None:
            typed = isinstance(gate, dict)
            command = gate.get("command") if typed else gate
            if typed:
                if not isinstance(command, str) or not command.strip():
                    err(f"{where}: typed gate needs a command")
                if gate.get("output") != "json":
                    err(f"{where}: typed gate needs output: json (use a plain string for a pass/fail command)")
                if structured:
                    err(f"{where}: typed gate cannot be combined with a returned schema (outputSchema); give out '{structured[0].id}' a path, or use a plain string gate")
            elif not isinstance(gate, str) or not gate.strip():
                err(f"{where}: verify.gate must be a command string or a typed gate mapping")
            check_refs(f"{where} gate", command)
            if isinstance(command, str):
                gate_scripts.extend((where, m) for m in GATES_REF_RE.findall(command))
            if literal_absolute(command):
                warn(f"{where}: gate command has an absolute path; use a ${{param}} or a workflow-relative path")
            if resumes is not None:
                err(f"{where}: gates are rejected on resumed children; verify in a following stage")
            if is_external:
                warn(f"{where}: external runner '{eff_agent}' does not support acceptance gates; run the check as its own script stage")
            if not run:
                err(f"{where}: a stage that launches no child cannot have a gate")

        if pattern in ("lanes", "council"):
            warn(f"{where}: pattern {pattern} is not compiled by compile_wave.py; the runner launches it by hand")
        if pattern in ("map", "map_reduce"):
            items = st.items
            if not items:
                err(f"{where}: {pattern} stages need `items`: the consumed input or output whose items are split into batches")
            elif items not in st.consumes:
                err(f"{where}: items '{items}' must also be listed in consumes")
            elif items in producer and not by_key[producer[items]].provisional:
                src = next((o for o in by_key[producer[items]].out if o.id == items), None)
                if not (src and src.path):
                    err(f"{where}: items '{items}' must be a file output (with a path) so it can be split")
            if gate is not None:
                err(f"{where}: gates on {pattern} stages are not supported (batches are merged by a command runner); verify in a following script stage")
            if kind == "deterministic":
                warn(f"{where}: deterministic work in a {pattern}; one script stage is usually better")
            b, max_batch = st.batch, plan.limits.max_batch
            if b is None or b <= 0:
                err(f"{where}: {pattern} stages need a positive integer batch")
            elif max_batch is not None and b > max_batch:
                err(f"{where}: batch {b} exceeds limits.max_batch {max_batch}")

    # --- outputs -----------------------------------------------------------
    for out in plan.outputs:
        where = f"output '{out.id}'"
        check_refs(where, out.location, is_path=out.kind == "file")
        if out.kind == "file" and literal_absolute(out.location):
            warn(f"{where}: absolute location; use a ${{param}} so the workflow can be moved")
        if out.id not in producer:
            err(f"{where}: no stage produces it"); continue
        pkey = producer[out.id]
        if (out.kind in ("app", "message") or out.overwrite) and by_key[pkey].kind != "side_effect":
            err(f"{where}: {out.kind}{' overwrite' if out.overwrite else ''} output must come from a side_effect stage (produced by '{pkey}')")

    # --- stage status (from the run) and waves (derived) --------------------------
    if "waves" in raw:
        err("plans do not list waves: they are derived from the stages (remove `waves`; give a stage "
            "`pause_after: true` if the user should review it before the stages that need it run)")
    for key, st in by_key.items():
        if "status" in (st.model_extra or {}):
            err(f"stage '{key}': status belongs to the run (runs/<id>/run.yaml), not the plan; remove it")
    run_id, rs = None, run_state
    if rs is None and workflow_root:
        act = active_run(workflow_root)
        if act:
            run_id, _, rs = act
    elif rs is not None:
        run_id = rs.get("id")
    rs = rs or {}
    stage_status = {k: v for k, v in (rs.get("stages") or {}).items() if v in ("running", "done")}
    for key in stage_status:
        if key not in by_key:
            warn(f"run {run_id}: stage '{key}' ran but is no longer in the plan")
    stage_status = {k: v for k, v in stage_status.items() if k in by_key}
    for key, sst in stage_status.items():
        if key in provisional:
            err(f"stage '{key}' is provisional but {sst} in run {run_id}; replan it before it runs")
        allowed = ("done",) if sst == "done" else ("done", "running")
        for d in needs[key]:
            if stage_status.get(d, "pending") not in allowed:
                err(f"stage '{key}' is {sst} in run {run_id} but needs '{d}', which is {stage_status.get(d, 'pending')}")

    # wave ids continue from the waves this run has finished
    first = 1 + sum(1 for r in rs.get("launches") or [] if isinstance(r, dict) and r.get("status") == "done")
    waves, level = [], {}
    if not cyclic:
        try:
            waves, level = derive_waves(by_key, needs, stage_status, first)
        except CycleError as exc:
            err("resolves_after makes a cycle: " + " -> ".join(reversed(exc.args[1]))
                + "; a provisional stage comes after the discovery it resolves after")
    running = [k for k, v in stage_status.items() if v == "running"]
    next_wave = waves[0] if waves and not running else None

    # A child's returned result only exists inside the workflow run that
    # produced it; later waves can only read files.
    for key, st in by_key.items():
        for cid in st.consumes if stage_status.get(key) != "done" else []:
            pkey = producer.get(cid)
            if pkey is None or pkey in provisional or not is_run_stage(by_key[pkey]):
                continue
            o = next((o for o in by_key[pkey].out if o.id == cid), None)
            if o and not o.path and (stage_status.get(pkey) == "done" or level.get(pkey, 0) < level.get(key, 0)):
                err(f"stage '{key}' consumes '{cid}', a returned result of stage '{pkey}' from an earlier wave; give that output a path so it survives the wave")

    if next_wave is not None:
        prov_next = [k for k in next_wave["stages"] if k in provisional]
        if prov_next and status == "ready":
            err(f"the next wave to run, '{next_wave['id']}', has provisional stages {prov_next}; replan before marking the plan ready")

    # --- unknowns ------------------------------------------------------------
    unresolved_unknowns = []
    q_ids = {q.id for q in plan.questions}
    for uid in duplicates(u.id for u in plan.unknowns):
        err(f"unknown '{uid}': duplicate id")
    for u in plan.unknowns:
        where, rb = f"unknown '{u.id}'", u.resolve_by
        if rb in ("planner", "probe"):
            if not u.finding:
                unresolved_unknowns.append(u.id)
                if status in ("needs_answers", "ready"):
                    err(f"{where}: resolve_by {rb} but no finding; resolve it before asking or finishing")
            if rb == "probe" and u.finding and not u.run_id:
                warn(f"{where}: probe finding has no run_id")
        elif rb == "user":
            if u.question not in q_ids:
                err(f"{where}: resolve_by user needs `question` naming an existing question")
        else:  # discovery
            sk = u.stage
            if sk not in by_key:
                err(f"{where}: resolve_by discovery needs `stage` naming an existing stage")
            elif sk in provisional:
                err(f"{where}: discovery stage '{sk}' is provisional")
            elif not u.finding and not any(by_key[p].resolves_after == sk for p in provisional):
                err(f"{where}: no provisional stage resolves_after '{sk}', so nothing replans on this discovery")
            for a in u.affects:
                if a not in by_key:
                    err(f"{where}: affects unknown stage '{a}'")

    # --- assumptions ---------------------------------------------------------
    for aid in duplicates(a.id for a in plan.assumptions):
        err(f"assumption '{aid}': duplicate id")
    confirm_count = sum(a.confirm for a in plan.assumptions)
    if confirm_count > 5:
        warn(f"{confirm_count} assumptions need confirmation; consider asking instead")

    # --- critique ------------------------------------------------------------
    critic_required = len(by_key) > 3 or any(st.kind in ("side_effect", "judgment") for st in by_key.values())
    c = plan.critique
    finding_ids = {f.id for f in c.findings} if c else set()
    asked_findings = [f.id for f in c.findings if f.disposition == "asked"] if c else []
    verdict = c.verdict if c else None
    if c:
        check_agent_ref("critique", c.agent)
        crev = revision if c.revision is None else c.revision
        if crev != revision:
            warn(f"critique is for revision {crev} but the plan is at revision {revision}; re-run the critic after replanning")
        for fid in duplicates(f.id for f in c.findings):
            err(f"critic finding '{fid}': duplicate id")
        for f in c.findings:
            if f.disposition in ("accepted", "rejected") and not f.note:
                err(f"critic finding '{f.id}': {f.disposition} findings need a note saying what changed or why not")
            if f.disposition == "rejected" and f.severity == "blocking":
                warn(f"critic finding '{f.id}' is blocking and was rejected; tell the user why")
        if verdict in ("revise", "ask_user") and not c.findings:
            err(f"critique.verdict is {verdict} but no findings are recorded")
    if critic_required and status in ("needs_answers", "ready") and c is None:
        err(f"status is {status} but the plan has no critique; it has side-effect, judgment or more than 3 stages, so run the plan critic first")

    # --- questions -----------------------------------------------------------
    for qid in duplicates(q.id for q in plan.questions):
        err(f"question '{qid}': duplicate id")
    open_q, covered_findings = [], set()
    for q in plan.questions:
        where = f"question '{q.id}'"
        for b in q.blocks:
            if b not in by_key:
                err(f"{where}: blocks unknown stage '{b}'")
        is_open = q.answer is None or (isinstance(q.answer, str) and not q.answer.strip())
        if is_open:
            open_q.append(q.id)
        if q.from_finding is not None:
            # an answered question may come from an earlier revision's critique (kept in the workflow's git history)
            if is_open and q.from_finding not in finding_ids:
                err(f"{where}: from_finding '{q.from_finding}' is not a recorded critic finding")
            covered_findings.add(q.from_finding)

    for fid in asked_findings:
        if fid not in covered_findings:
            err(f"critic finding '{fid}' is marked asked but no question has from_finding: {fid}")

    if status == "ready" and open_q:
        err(f"status is ready but questions {open_q} are unanswered; answer them or convert them to assumptions")
    if status == "needs_answers" and not open_q:
        err("status is needs_answers but no question is open")
    if len(open_q) > 3:
        warn(f"{len(open_q)} open questions; ask at most 3 per round")

    # --- params and their references ------------------------------------------
    env_names = {}
    for name, spec in params.items():
        env = workflow_params.env_name(name, spec)
        if not workflow_params.ENV_NAME_RE.match(env):
            err(f"param '{name}': '{env}' is not a valid environment variable name; set env: to one (letters, digits, _)")
        elif workflow_params.reserved(env):
            err(f"param '{name}': reads ${env}, which the system or a tool already uses; set env: to a name of its own")
        if env in env_names:
            err(f"param '{name}': reads ${env}, like param '{env_names[env]}'")
        env_names.setdefault(env, name)
        if spec.type == "secret" and spec.default not in (None, ""):
            err(f"param '{name}': a secret has no default; its value stays in the environment or .env")
    for where, ref, is_path in param_refs:
        if ref not in params:
            err(f"{where}: references undefined param '${{{ref}}}'")
        elif is_path and params[ref].type == "secret":
            err(f"{where}: a secret (param '{ref}') cannot be part of a path")
    used = {ref for _, ref, _ in param_refs}
    for name, spec in params.items():
        if name not in used and spec.type != "secret":  # a secret may be read only from the environment (tools, scripts)
            warn(f"param '{name}' is declared but never used")

    # --- workflow ------------------------------------------------------------
    workflow_info = None
    if workflow_root:
        workflow_info = {"root": os.path.abspath(workflow_root)}
        for rel, what in (
            ("AGENTS.md", "workflow context for Pi"),
            (os.path.join(".pi", "APPEND_SYSTEM.md"), "the workflow agent's role"),
            (os.path.join(".pi", "extensions", "workflow", "index.ts"), "the /workflow-* commands and workflow_* tools"),
            (os.path.join(".pi", "skills", "workflow", "SKILL.md"), "the planning skill used at replan"),
            (os.path.join(".pi", "settings.json"), "project package declarations"),
            (os.path.join(".pi", "agents", "workflow-batcher.md"), "the command runner map stages use"),
            (os.path.join(".pi", "skills", "workflow", "scripts", "compile_wave.py"), "the wave compiler"),
        ):
            if not os.path.exists(os.path.join(workflow_root, rel)):
                warn(f"workflow: {rel} is missing ({what}); re-run scaffold_workflow.py --refresh")
        for where, script in gate_scripts:
            if not os.path.exists(os.path.join(workflow_root, "gates", script)):
                (err if status == "ready" else warn)(
                    f"{where}: gate script gates/{script} does not exist; write it while planning (never in the stage it checks)")
        on_disk = project_agent_names(workflow_root)
        for name, src in agent_sources.items():
            if src == "project" and name not in on_disk:
                err(f"workflow: agent '{name}' has source project but no .pi/agents/*.md defines it")
        if os.path.exists(os.path.join(workflow_root, ".pi", "params.yaml")):
            warn("workflow: .pi/params.yaml is no longer read (values live in .env); /workflow-refresh in Prometheus moves them")
        valid = {n: s for n, s in params.items() if workflow_params.ENV_NAME_RE.match(workflow_params.env_name(n, s))}
        values = workflow_params.load(workflow_root, valid)
        unset = workflow_params.missing(valid, values)
        workflow_info["missing_params"] = [n for n in unset if params[n].type != "secret"]
        workflow_info["missing_secrets"] = [workflow_params.env_name(n, params[n]) for n in unset if params[n].type == "secret"]
        # Input files and folders live outside the workflow; children run in work/, so a
        # relative path would silently point inside the workflow.
        for inp in plan.inputs:
            loc = inp.location or ""
            refs = PARAM_REF_RE.findall(loc)
            if inp.kind not in ("folder", "file") or not refs or any(values.get(r) is None or r not in valid for r in refs):
                continue  # no param, or not set yet (reported as missing)
            path = PARAM_REF_RE.sub(lambda m: str(values[m.group(1)]), loc)
            full = os.path.expanduser(path)
            if not os.path.isabs(full):
                err(f"workflow: input '{inp.id}' resolves to '{path}', a relative path; set an absolute path in .env (~/… is fine)")
            elif not os.path.exists(full):
                err(f"workflow: input '{inp.id}' resolves to '{path}', which does not exist; check the value in .env")

    summary = {
        "revision": revision,
        "stages": len(by_key),
        "run": {"id": run_id, "status": rs.get("status")} if run_id else None,
        "last_run": last_run(workflow_root) if workflow_root else None,
        "done": [k for k, v in stage_status.items() if v == "done"],
        "running": running,
        "provisional": sorted(provisional),
        "next_wave": next_wave["id"] if next_wave else None,
        "waves": waves,
        "open_questions": len(open_q),
        "unresolved_unknowns": unresolved_unknowns,
        "approvals": approvals,
        "workflow_keys": keys_summary,
        "agents_checked": agent_names is not None,
        "critic_required": critic_required,
        "critic_verdict": verdict,
    }
    if workflow_info is not None:
        summary["workflow"] = workflow_info
    return errors, warnings, summary


def main(
    plan_path: Annotated[str, typer.Argument(metavar="PLAN.yaml")],
    agents: Annotated[str | None, typer.Option(help="comma-separated agent names from subagent list")] = None,
    external: Annotated[str | None, typer.Option(help="comma-separated names of agents on external runners")] = None,
    workflow: Annotated[str | None, typer.Option(help="workflow root (default: detected from <workflow>/.pi/plan.yaml)")] = None,
):
    """Validate a workflow YAML file."""
    try:
        with open(plan_path, encoding="utf-8") as fh:
            plan = load_yaml(fh)
    except (OSError, YAMLError) as exc:
        print(json.dumps({"valid": False, "errors": [f"cannot read plan: {exc}"], "warnings": []}))
        sys.exit(2)
    if workflow is None:
        parent = os.path.dirname(os.path.abspath(plan_path))
        if os.path.basename(parent) == ".pi":
            workflow = os.path.dirname(parent)
    errors, warnings, summary = validate(plan, agents, external, workflow)
    print(json.dumps({
        "valid": not errors,
        "status": plan.get("status") if isinstance(plan, dict) else None,
        "errors": errors,
        "warnings": warnings,
        "summary": summary,
    }, indent=2))
    sys.exit(0 if not errors else 1)


if __name__ == "__main__":
    typer.run(main)
