"""Releases (release.py) and the frozen compile they rely on (compile_wave.py --frozen): an agent code can run, with
no person or model deciding anything, and the interface its callers depend on."""
import shutil
import sys

import pytest
from conftest import SKILL, copy_example, git_in, stamp

SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
import release as release_py

PLAN = """\
version: 1
revision: 1
status: ready
request: "count the lines"
goal: Count the lines of a file and save the count.
done_when:
  - The count is saved where the user asked.
params:
  source:
    description: The file to count
  report:
    description: Where to save the count
    default: outputs/count.txt
outputs:
  - id: count_file
    kind: file
    location: ${report}
    format: txt
agents:
  checked: test
  available: [worker, {name: plan-critic, source: project}]
stages:
  - id: count
    title: Count the lines
    kind:
      value: deterministic
      reason: A program can count lines.
    pattern: script
    agent: worker
    program:
      file: count.py
      args: "${source} count.json"
    writes:
      - id: count
        path: count.json
    done_when: count.json holds one number
    gate: "true"
  - id: save
    title: Save the count
    kind:
      value: side_effect
      reason: It writes the user's file.
    pattern: script
    agent: worker
    program:
      file: save.py
      args: "count.json ${report}"
    after: [count]
    reads: [count]
    writes:
      - id: count_file
        path: ${report}
    done_when: the report holds the count
    gate: "true"
"""


PROGRAMS = {  # count.py <file> <json out>: the file's line count; save.py <json in> <report>: writes it
    "count.py": "import json, sys\njson.dump(len(open(sys.argv[1]).read().splitlines()), open(sys.argv[2], 'w'))\n",
    "save.py": "import json, sys\nfrom pathlib import Path\np = Path(sys.argv[2])\np.parent.mkdir(parents=True, exist_ok=True)\n"
               "p.write_text(str(json.load(open(sys.argv[1]))) + '\\n')\n",
}


def make_agent(root, run_py, slug="counter"):
    """A ready, committed agent whose programs exist (no stage needs a model): releasable."""
    r = run_py([SCRIPTS / "scaffold_agent.py", slug, "--root", root, "--request", "count the lines"])
    assert r.data and r.data["ok"], r
    folder = root / slug
    (folder / "plan.yaml").write_text(PLAN, encoding="utf-8")
    stamp(folder / "plan.yaml")
    (folder / "scripts").mkdir(exist_ok=True)
    for name, text in PROGRAMS.items():
        (folder / "scripts" / name).write_text(text, encoding="utf-8")
    commit(folder)
    return folder


@pytest.fixture
def agent(tmp_path, run_py):
    return make_agent(tmp_path, run_py)


def commit(folder, message="test"):
    git = git_in(folder)
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message)


def release(run_py, folder, *args):
    return run_py([SCRIPTS / "release.py", *args, "--agent", folder], cwd=folder)


def edit_plan(folder, old, new):
    text = (folder / "plan.yaml").read_text(encoding="utf-8")
    assert old in text
    (folder / "plan.yaml").write_text(text.replace(old, new), encoding="utf-8")
    stamp(folder / "plan.yaml")
    commit(folder)


def test_a_ready_agent_releases_as_v1_with_its_interface(agent, run_py):
    r = release(run_py, agent, "check")
    assert r.data["releasable"], r.data["problems"]
    assert r.data["interface"] == {
        "params": {"source": {"type": "path", "required": True}, "report": {"type": "path", "required": False}},
        "outputs": {"count_file": {"kind": "file", "format": "txt"}}, "side_effects": ["save"], "decisions": []}
    assert r.data["model_stages"] == []  # both stages are program commands
    t = release(run_py, agent, "tag")
    assert t.data["ok"] and t.data["version"] == "v1.0", t
    listed = release(run_py, agent, "list").data["releases"]
    assert [(x["version"], x["revision"], x["breaking"]) for x in listed] == [("v1.0", 1, False)]
    assert release(run_py, agent, "tag").data["error"] == "this commit is already released as v1.0"


def test_a_change_that_can_break_callers_needs_breaking_and_raises_the_major(agent, run_py):
    assert release(run_py, agent, "tag").data["version"] == "v1.0"
    edit_plan(agent, "  report:\n", "  label:\n    description: A label\n    type: text\n  report:\n")
    r = release(run_py, agent, "check")
    assert r.data["changes"]["breaking"] == ["param label added (required)"] and r.data["next_version"] == "v2.0"
    refused = release(run_py, agent, "tag")
    assert refused.returncode == 1 and "--breaking" in refused.data["error"]
    assert release(run_py, agent, "tag", "--breaking").data["version"] == "v2.0"
    edit_plan(agent, "    description: A label\n", "    description: A label\n    default: none\n")
    r = release(run_py, agent, "check")
    assert r.data["changes"] == {"breaking": [], "other": ["param label is now optional"]} and r.data["next_version"] == "v2.1"
    # a break the interface can't show: the author says so, and the major goes up all the same
    assert release(run_py, agent, "check", "--breaking").data["next_version"] == "v3.0"
    assert release(run_py, agent, "tag", "--breaking").data["version"] == "v3.0"


def test_a_new_decision_breaks_callers_but_not_against_an_older_release():
    old = {"params": {}, "outputs": {}, "side_effects": [], "decisions": []}
    assert release_py.compare(old, {**old, "decisions": ["pick"]})[0] == ["decision pick added (the run stops to ask its caller)"]
    before = {"params": {}, "outputs": {}, "side_effects": []}  # tagged before decisions were in the interface
    assert release_py.compare(before, {**old, "decisions": ["pick"]}) == ([], [])


def test_what_a_released_run_could_not_do_blocks_the_release(agent, run_py, tmp_path):
    (agent / "scripts" / "save.py").unlink()
    commit(agent)
    problems = release(run_py, agent, "check").data["problems"]
    assert any("scripts/save.py doesn't exist" in p for p in problems), problems
    (agent / "scratch.txt").write_text("x")
    assert any("uncommitted changes" in p for p in release(run_py, agent, "check").data["problems"])
    other = tmp_path / "memo"
    shutil.copytree(agent, other)
    (other / "scratch.txt").unlink()
    copy_example("competitor-memo", other)
    commit(other)
    problems = " ".join(release(run_py, other, "check").data["problems"])
    assert "is provisional" in problems and "is a decision" not in problems  # a decision is asked of the caller


def test_the_frozen_compile_is_one_wave_when_approved_else_wave_by_wave(agent, run_py, tmp_path):
    run_dir = tmp_path / "call-1"
    args = [SCRIPTS / "compile_wave.py", "--agent", agent, "--frozen", run_dir, "--params", '{"source": "/data/in.txt"}']
    first = run_py(args)  # save not approved: count runs first, and the run asks before save
    assert first.data["ok"] and (first.data["wave"], first.data["stages"]) == ("w1", ["count"]), first
    (run_dir / "state.json").write_text('{"done": ["count"], "launches": [{"wave": "w1"}]}')
    asks = run_py(args)
    assert asks.returncode == 1 and [a["stage"] for a in asks.data["preflight"]["approvals_needed"]] == ["save"]
    (run_dir / "state.json").write_text('{"done": ["count", "save"], "launches": [{"wave": "w1"}, {"wave": "w2"}]}')
    assert run_py(args).data == {"ok": True, "done": True}
    (run_dir / "state.json").unlink()
    r = run_py([*args, "--approve", "save"])
    assert r.data["ok"] and r.data["wave"] == "all" and r.data["stages"] == ["count", "save"], r
    assert r.data["call"]["workflow"] == str((run_dir / ".compiled" / "all.js").resolve())
    script = (run_dir / ".compiled" / "all.js").read_text()
    assert "/data/in.txt" in script and str(run_dir / "outputs" / "count.txt") in script  # relative values resolve in the run
    assert git_in(agent)("status", "--porcelain").stdout == ""  # nothing committed or written in the agent
    secret = run_py([SCRIPTS / "compile_wave.py", "--agent", agent, "--frozen", run_dir, "--params", '{"nope": 1}'])
    assert secret.returncode == 2 and "declares no param 'nope'" in secret.data["error"]
