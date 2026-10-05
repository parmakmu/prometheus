"""A model stage with no side effect whose inputs and definition are unchanged since an earlier run
finished it takes that run's outputs instead of running again (compile_wave reuse_earlier)."""
import importlib.util
import json
import sys

from conftest import SKILL

sys.path.insert(0, str(SKILL / "scripts"))  # compile_wave imports its sibling scripts
spec = importlib.util.spec_from_file_location("compile_wave", SKILL / "scripts" / "compile_wave.py")
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)

STAGES = [
    {"key": "discover", "title": "Inspect the emails", "kind": "judgment", "agent": "scout", "consumes": ["emails"],
     "out": [{"id": "report", "path": "discovery.md"}], "verify": {"review": "reviewer", "check": "Report the fields."}},
    {"key": "summarise", "title": "Summarise", "kind": "judgment", "agent": "worker", "needs": ["discover"],
     "consumes": ["report"], "out": [{"id": "summary", "path": "summary.md"}], "verify": {"check": "One line per field."}},
    {"key": "send", "title": "Send the summary", "kind": "side_effect", "agent": "worker", "needs": ["summarise"],
     "consumes": ["summary"], "out": [{"id": "sent", "path": "sent.json"}], "verify": {"check": "Sent once."}},
]
WAVE = {"id": "w1", "stages": ["discover", "summarise", "send"]}


def plan(mail):
    return {"stages": STAGES, "inputs": [{"id": "emails", "kind": "folder", "location": str(mail)}]}


def compile_run(tmp_path, rid):
    run_dir = tmp_path / "runs" / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    c = cw.Compiler(tmp_path, plan(tmp_path / "mail"), {}, run_dir, {}, {"send"})
    return c, c.compile(WAVE, reuse=True)


def finish(c, rid):
    """What a finished run leaves: its stages done, fingerprints, the wave's result, the outputs."""
    run_dir = c.run_dir
    (run_dir / "run.yaml").write_text("status: done\nstages:\n" + "".join(f"  {s['key']}: done\n" for s in STAGES))
    (run_dir / ".compiled").mkdir(exist_ok=True)
    (run_dir / ".compiled" / "w1.inputs.json").write_text(json.dumps(c.fingerprints))
    children = {s["key"]: {"status": "completed", "runId": f"{rid}-{s['key']}"} for s in STAGES}
    (run_dir / ".compiled" / "w1-result.json").write_text(json.dumps({"status": "done", "children": children}))
    (run_dir / "work").mkdir(exist_ok=True)
    for name in ("discovery.md", "summary.md", "sent.json"):
        (run_dir / "work" / name).write_text(f"{name} of {rid}")


def items(result):
    return {it["key"]: it for step in result["steps"] if "layer" in step for it in step["layer"]}


def setup_first_run(tmp_path):
    (tmp_path / "mail").mkdir()
    (tmp_path / "mail" / "a.eml").write_text("Subject: hi\n\nhello")
    c, first = compile_run(tmp_path, "2026-01-01-1")
    assert first["ok"] and not any("reuse" in it for it in items(first["spec"]).values())
    finish(c, "2026-01-01-1")


def test_unchanged_judgments_are_reused_and_not_reviewed_again(tmp_path):
    setup_first_run(tmp_path)
    c, again = compile_run(tmp_path, "2026-01-01-2")
    got = items(again["spec"])
    assert got["discover"]["reuse"]["run"] == got["summarise"]["reuse"]["run"] == "2026-01-01-1"
    assert got["discover"]["reuse"]["runId"] == "2026-01-01-1-discover"
    assert not any("review" in step for step in again["spec"]["steps"]) and "discover-review" not in again["children"]
    c.copy_reused()
    assert (c.work / "discovery.md").read_text() == "discovery.md of 2026-01-01-1"


def test_a_side_effect_always_runs(tmp_path):
    setup_first_run(tmp_path)
    _, again = compile_run(tmp_path, "2026-01-01-2")
    assert "reuse" not in items(again["spec"])["send"]


def test_a_stage_whose_subagent_has_a_connector_runs_again(tmp_path):
    (tmp_path / ".pi" / "agents").mkdir(parents=True)
    (tmp_path / ".pi" / "agents" / "worker.md").write_text("---\nname: worker\ntools: read, write, mcp:slack\n---\nWorks.\n")
    setup_first_run(tmp_path)
    _, again = compile_run(tmp_path, "2026-01-01-2")
    got = items(again["spec"])
    assert "reuse" in got["discover"] and "reuse" not in got["summarise"]


def test_a_changed_input_runs_the_stage_and_those_after_it(tmp_path):
    setup_first_run(tmp_path)
    (tmp_path / "mail" / "b.eml").write_text("Subject: new\n\nmore")
    _, again = compile_run(tmp_path, "2026-01-01-2")
    assert not any("reuse" in it for it in items(again["spec"]).values())
    assert any("review" in step for step in again["spec"]["steps"])


def test_a_changed_stage_runs_again(tmp_path):
    setup_first_run(tmp_path)
    STAGES[0]["verify"]["check"] = "Report the fields and their encodings."
    try:
        _, again = compile_run(tmp_path, "2026-01-01-2")
    finally:
        STAGES[0]["verify"]["check"] = "Report the fields."
    assert not any("reuse" in it for it in items(again["spec"]).values())


def test_a_planning_check_reuses_nothing(tmp_path):
    setup_first_run(tmp_path)
    c = cw.Compiler(tmp_path, plan(tmp_path / "mail"), {}, tmp_path / "runs" / "2026-01-01-2", {}, {"send"})
    assert not any("reuse" in it for it in items(c.compile(WAVE)["spec"]).values())


def test_the_same_input_moved_elsewhere_is_still_reused(tmp_path):
    # only the input's contents count, not where it lives
    setup_first_run(tmp_path)
    (tmp_path / "uploads").mkdir()
    (tmp_path / "mail").rename(tmp_path / "uploads" / "mail")
    run_dir = tmp_path / "runs" / "2026-01-01-2"
    run_dir.mkdir(parents=True)
    again = cw.Compiler(tmp_path, plan(tmp_path / "uploads" / "mail"), {}, run_dir, {}, {"send"}).compile(WAVE, reuse=True)
    assert items(again["spec"])["discover"]["reuse"]["run"] == "2026-01-01-1"
