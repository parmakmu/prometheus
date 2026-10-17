#!/usr/bin/env python3
"""Releases: a version of an agent that code can run (embedded, or as a service), with no person and no model
deciding anything at run time.

A release is the tag v<major>.<minor> on a commit of main, made only when the plan can run that way:
  - it is ready (the critic recorded for this plan), on main, with nothing uncommitted and no active run;
  - no stage is provisional (its run would resolve: that is editing the plan, which only the agent's own runs do);
  - every stage program (`script:`) exists (a released run never writes code);
  - every wave compiles as a released run compiles it (run_compile.compile_frozen). A decision or a side effect
    is fine: the run asks its caller there (A2A's input-required), and goes on with the answer.
Its interface is what a caller depends on: the params (type, required or not), the outputs (kind, format) and the
side effects a caller approves. Against the last release, a change that can break a caller (a param removed, retyped
or newly required, an output removed or changed, a side effect added) needs --breaking and raises the major
version; anything else raises the minor. The tag's message holds the revision, the interface and whether it broke.

Usage:
  agent_release.py check [--agent DIR]               whether HEAD can be released, its interface and the changes
  agent_release.py tag [--agent DIR] [--breaking]    tag it
  agent_release.py list [--agent DIR]                the releases, newest first
"""
import json
import re
from pathlib import Path
from typing import Annotated

import agent_params
import agent_repo
import typer
from agent_state import state
from plan_validate import check_ready, validate
from run_compile import compile_frozen
from utils import emit, read_plan, refuse, stage_kind

TAG_RE = re.compile(r"^v(\d+)\.(\d+)$")
app = typer.Typer(add_completion=False)


def interface(plan):
    """What a caller of a release depends on."""
    params = {}
    for name, raw in (plan.get("params") or {}).items():
        spec = agent_params.spec_of(raw)
        has_default = spec.get("default") not in (None, "")
        params[name] = {"type": agent_params.kind(spec), "required": bool(spec.get("required", True)) and not has_default}
    outputs = {o["id"]: {"kind": o.get("kind"), "format": o.get("format")}
               for o in plan.get("outputs") or [] if isinstance(o, dict) and o.get("id")}
    stages = [s for s in plan.get("stages") or [] if isinstance(s, dict)]
    side_effects = sorted(s["id"] for s in stages if stage_kind(s) == "side_effect")
    # where a run stops to ask its caller (a decision): a caller that never had to answer must now
    decisions = sorted(s["id"] for s in stages if stage_kind(s) == "decision" or s.get("pattern") == "approval")
    return {"params": params, "outputs": outputs, "side_effects": side_effects, "decisions": decisions}


def compare(old, new):
    """(breaking, other): the changes from interface `old` to `new`, in words."""
    breaking, other = [], []
    for name, spec in old["params"].items():
        now = new["params"].get(name)
        if now is None:
            breaking.append(f"param {name} removed")
        elif now["type"] != spec["type"]:
            breaking.append(f"param {name}: type {spec['type']} -> {now['type']}")
        elif now["required"] and not spec["required"]:
            breaking.append(f"param {name} is now required")
        elif spec["required"] and not now["required"]:
            other.append(f"param {name} is now optional")
    for name, spec in new["params"].items():
        if name not in old["params"]:
            (breaking if spec["required"] else other).append(f"param {name} added ({'required' if spec['required'] else 'optional'})")
    for oid, spec in old["outputs"].items():
        now = new["outputs"].get(oid)
        if now is None:
            breaking.append(f"output {oid} removed")
        elif now != spec:
            breaking.append(f"output {oid}: {spec} -> {now}")
    other += [f"output {oid} added" for oid in new["outputs"] if oid not in old["outputs"]]
    breaking += [f"side effect {s} added (callers must approve it)" for s in new["side_effects"] if s not in old["side_effects"]]
    other += [f"side effect {s} removed" for s in old["side_effects"] if s not in new["side_effects"]]
    if "decisions" in old:  # a release from before decisions were part of the interface: nothing to compare
        breaking += [f"decision {s} added (the run stops to ask its caller)" for s in new["decisions"] if s not in old["decisions"]]
        other += [f"decision {s} removed" for s in old["decisions"] if s not in new["decisions"]]
    return breaking, other


def releases(agent_dir):
    """[{version, commit, revision, breaking, interface}], newest first."""
    if not agent_repo.is_repo(agent_dir):
        return []
    # three NUL-ended fields per tag: name, the commit it points at, its message
    out = agent_repo.git(agent_dir, "for-each-ref", "refs/tags/v*", "--format=%(refname:short)%00%(*objectname)%00%(contents)%00",
                        check=False).stdout.split("\x00")
    found = []
    for i in range(0, len(out) - 2, 3):
        name, commit, message = out[i].strip(), out[i + 1], out[i + 2]
        if not (m := TAG_RE.match(name)):
            continue
        try:
            meta = json.loads(message)
        except ValueError:
            meta = {}
        found.append({"version": name, "commit": commit, "key": (int(m[1]), int(m[2])), **meta})
    found.sort(key=lambda r: r["key"], reverse=True)
    return [{k: v for k, v in r.items() if k != "key"} for r in found]


def problems_with(agent_dir, plan):
    """Why HEAD can't be released (empty when it can)."""
    agent_dir = Path(agent_dir)
    found = []
    where = state(agent_dir)
    if where["state"] not in ("main_ready", "plan_ready"):  # plan_ready: no run yet
        found.append(f"the agent is {where['state']} ({where['hint']}): release from main with a ready plan and no active run")
    if agent_repo.is_repo(agent_dir) and agent_repo.git(agent_dir, "status", "--porcelain").stdout.strip():
        found.append("there are uncommitted changes: a release is a commit, so commit or discard them first")
    if why := check_ready(plan):
        found.append(why)
    errors, _warnings, summary = validate(plan, None, None, agent_dir)
    found += [f"plan: {e}" for e in errors]
    stages = [s for s in plan.get("stages") or [] if isinstance(s, dict) and s.get("id")]
    found += [f"stage {key} is provisional: its run would resolve; run the agent until the plan is settled, then release"
              for key in summary.get("provisional") or []]
    programs = {s["id"]: (s.get("program") or {}).get("file") for s in stages}
    found += [f"stage {key}'s program scripts/{file} doesn't exist yet: a released run never writes code; "
              "run the agent once so it is written and reviewed" for key, file in programs.items() if file and not (agent_dir / "scripts" / file).is_file()]
    if not found:
        found += compile_problems(agent_dir, plan)
    return found


def compile_problems(agent_dir, plan):
    """Compile every wave a released run would launch, in order, as if each before it had run (decisions and approvals
    are what the run asks its caller, not problems: only compile errors are)."""
    values = {name: "release-check" for name in plan.get("params") or {}}
    state, found = {"done": [], "launches": []}, []
    for _ in range(len(plan.get("stages") or []) + 1):
        result = compile_frozen(agent_dir, plan, agent_dir / ".release-check", values, set(), state)
        if result.get("done"):
            break
        found += [f"compile {result.get('wave')}: {e}" for e in result.get("errors") or []]
        if not result.get("stages"):
            break
        state["done"] += result["stages"]
        state["launches"].append({"wave": result.get("wave")})
    return found


def model_stages(plan):
    """The stages a released run pays a model for: those not run as a program command, and their reviews."""
    out = []
    for s in plan.get("stages") or []:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        program = s.get("program") or {}
        if not (program.get("file") and program.get("args") is not None) and s.get("pattern") != "a2a":  # a2a: the batcher calls an agent
            out.append(s["id"])
        if s.get("review"):
            out.append(f"{s['id']}-review")
    return out


def report(agent_dir, breaking_flag=False):
    """`breaking_flag`: the author says it can break callers (a change the interface doesn't show): major + 1 too."""
    agent_dir = Path(agent_dir).resolve()
    plan = read_plan(agent_dir, strict=True) or {}
    found = problems_with(agent_dir, plan)
    face = interface(plan)
    last = next(iter(releases(agent_dir)), None)
    head = agent_repo.head(agent_dir)
    if last:
        breaking, other = compare(last.get("interface") or {"params": {}, "outputs": {}, "side_effects": []}, face)
        major, minor = map(int, TAG_RE.match(last["version"]).groups())
        version = f"v{major + 1}.0" if breaking or breaking_flag else f"v{major}.{minor + 1}"
    else:
        breaking, other, version = [], ["first release"], "v1.0"
    already = next((r["version"] for r in releases(agent_dir) if r["commit"] == head), None)
    return {"ok": True, "releasable": not found and not already, "problems": found, "already_released": already,
                  "next_version": version, "last_release": last and last["version"], "revision": plan.get("revision", 1),
                  "interface": face, "changes": {"breaking": breaking, "other": other}, "model_stages": model_stages(plan)}


@app.command()
def check(agent_dir: Annotated[str, typer.Option("--agent", help="agent root")] = ".",
          breaking: Annotated[bool, typer.Option("--breaking", help="it can break callers: the next version is a major one")] = False):
    """Whether HEAD can be released, its interface and what changed since the last release."""
    emit(report(agent_dir, breaking))


@app.command()
def tag(agent_dir: Annotated[str, typer.Option("--agent", help="agent root")] = ".",
        breaking: Annotated[bool, typer.Option("--breaking", help="release a change that can break callers (raises the major version)")] = False):
    """Tag HEAD as the next release."""
    out = report(agent_dir, breaking)
    if out["already_released"]:
        refuse(f"this commit is already released as {out['already_released']}")
    if out["problems"]:
        refuse("can't release: " + "; ".join(out["problems"]), problems=out["problems"])
    if out["changes"]["breaking"] and not breaking:
        refuse("this release can break callers (" + "; ".join(out["changes"]["breaking"]) + "): release it with --breaking, "
               "so the major version goes up and pinned callers keep the old one", changes=out["changes"])
    message = json.dumps({"revision": out["revision"], "breaking": bool(out["changes"]["breaking"]) or breaking, "interface": out["interface"]})
    root = Path(agent_dir).resolve()
    agent_repo.git(root, *agent_repo.identity(root), "tag", "-a", out["next_version"], "-m", message)
    emit({"ok": True, "version": out["next_version"], "commit": agent_repo.head(root), "changes": out["changes"],
          "interface": out["interface"], "model_stages": out["model_stages"]})


@app.command("list")
def list_releases(agent_dir: Annotated[str, typer.Option("--agent", help="agent root")] = "."):
    """The agent's releases, newest first."""
    emit({"ok": True, "releases": releases(Path(agent_dir).resolve())})


if __name__ == "__main__":
    app()
