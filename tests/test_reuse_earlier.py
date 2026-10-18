"""A model stage with no side effect whose inputs and definition are unchanged since an earlier run
finished it takes that run's outputs instead of running again (compile_wave reuse_earlier)."""
import importlib.util
import json
import sys

from conftest import SKILL

sys.path.insert(0, str(SKILL / "scripts"))  # compile_wave imports its sibling scripts
spec = importlib.util.spec_from_file_location("compile_wave", SKILL / "scripts" / "run_compile.py")
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)

STAGES = [
    {"id": "discover", "title": "Inspect the emails", "kind": {"value": "judgment"}, "agent": "scout", "reads": ["emails"],
     "writes": [{"id": "report", "path": "discovery.md"}], "review": "reviewer", "done_when": "Report the fields."},
    {"id": "summarise", "title": "Summarise", "kind": {"value": "judgment"}, "agent": "worker", "after": ["discover"],
     "reads": ["report"], "writes": [{"id": "summary", "path": "summary.md"}], "done_when": "One line per field."},
    {"id": "send", "title": "Send the summary", "kind": {"value": "side_effect"}, "agent": "worker", "after": ["summarise"],
     "reads": ["summary"], "writes": [{"id": "sent", "path": "sent.json"}], "done_when": "Sent once."},
]
WAVE = {"id": "w1", "stages": ["discover", "summarise", "send"]}


def plan(mail):
    return {"stages": STAGES, "inputs": [{"id": "emails", "kind": "folder", "path": str(mail)}]}


def compile_run(tmp_path, rid):
    run_dir = tmp_path / "runs" / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    c = cw.Compiler(tmp_path, plan(tmp_path / "mail"), {}, run_dir, {}, {"send"})
    return c, c.compile(WAVE, reuse=True)


def finish(c, rid):
    """What a finished run leaves: its stages done, fingerprints, the wave's result, the outputs."""
    run_dir = c.run_dir
    (run_dir / "run.yaml").write_text("status: done\nstages:\n" + "".join(f"  {s['id']}: done\n" for s in STAGES))
    children = {s["id"]: {"status": "completed", "runId": f"{rid}-{s['id']}"} for s in STAGES}
    cw.write_wave(run_dir, "w1", {"inputs": c.fingerprints, "result": {"status": "done", "children": children}})
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
    STAGES[0]["done_when"] = "Report the fields and their encodings."
    try:
        _, again = compile_run(tmp_path, "2026-01-01-2")
    finally:
        STAGES[0]["done_when"] = "Report the fields."
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


MAP_STAGES = [
    {"id": "summarise", "title": "Summarise each email", "kind": {"value": "judgment"}, "pattern": "map", "agent": "reader",
     "reads": ["emails"], "map": {"over": "emails", "glob": "*.eml", "batch": 2},
     "writes": [{"id": "summaries", "path": "summaries.jsonl"}], "done_when": "One line per email."},
    {"id": "digest", "title": "Write the digest", "kind": {"value": "judgment"}, "agent": "worker", "after": ["summarise"],
     "reads": ["summaries"], "writes": [{"id": "digest", "path": "digest.md"}], "done_when": "A paragraph."},
]


def compile_map_run(tmp_path, rid, batch=2):
    run_dir = tmp_path / "runs" / rid
    run_dir.mkdir(parents=True, exist_ok=True)
    stages = [{**MAP_STAGES[0], "map": {**MAP_STAGES[0]["map"], "batch": batch}}, MAP_STAGES[1]]
    c = cw.Compiler(tmp_path, {"stages": stages, "inputs": [{"id": "emails", "kind": "folder", "path": str(tmp_path / "mail")}]},
                    {}, run_dir, {}, set())
    return c, c.compile({"id": "w1", "stages": ["summarise", "digest"]}, reuse=True)


def finish_map(c, rid, reused=False):
    """A finished run of the map plan: split, two batches and the merge completed (or the map reused), then the digest."""
    (c.run_dir / "run.yaml").write_text("status: done\nstages:\n  summarise: done\n  digest: done\n")
    children = ({"summarise": {"status": "completed", "reused": True}} if reused else
                {k: {"status": "completed", "runId": f"{rid}-{k}"} for k in ("summarise-split", "summarise-0", "summarise-1", "summarise-merge")})
    children["digest"] = {"status": "completed", "runId": f"{rid}-digest"}
    cw.write_wave(c.run_dir, "w1", {"inputs": c.fingerprints, "result": {"status": "done", "children": children}})
    (c.run_dir / "work").mkdir(exist_ok=True)
    for name in ("summaries.jsonl", "digest.md"):
        (c.run_dir / "work" / name).write_text(f"{name} of {rid}")


def test_a_map_stage_on_the_same_items_is_reused_whole_and_so_is_what_reads_it(tmp_path):
    (tmp_path / "mail").mkdir()
    for n in range(3):
        (tmp_path / "mail" / f"{n}.eml").write_text(f"Subject: {n}\n\nbody {n}")
    c, first = compile_map_run(tmp_path, "2026-01-01-1")
    assert first["ok"] and any("map" in step for step in first["spec"]["steps"]) and "summarise" in c.fingerprints
    finish_map(c, "2026-01-01-1")

    c, again = compile_map_run(tmp_path, "2026-01-01-2")
    assert not any("map" in step for step in again["spec"]["steps"])  # no split, batches or merge launched
    got = items(again["spec"])
    assert got["summarise"]["reuse"]["run"] == "2026-01-01-1" and got["digest"]["reuse"]["run"] == "2026-01-01-1"
    assert again["children"] == ["summarise", "digest"]
    c.copy_reused()
    assert (c.work / "summaries.jsonl").read_text() == "summaries.jsonl of 2026-01-01-1"
    finish_map(c, "2026-01-01-2", reused=True)
    assert items(compile_map_run(tmp_path, "2026-01-01-3")[1]["spec"])["summarise"]["reuse"]["run"] == "2026-01-01-2"  # a reuse reused

    (tmp_path / "mail" / "3.eml").write_text("Subject: new\n\nmore")  # one item more: mapped again, and so is what reads it
    _, grown = compile_map_run(tmp_path, "2026-01-01-4")
    assert any("map" in step for step in grown["spec"]["steps"]) and "reuse" not in items(grown["spec"])["digest"]
    (tmp_path / "mail" / "3.eml").unlink()
    _, rebatched = compile_map_run(tmp_path, "2026-01-01-5", batch=3)  # another batch size: mapped again
    assert any("map" in step for step in rebatched["spec"]["steps"])


def test_a_stage_writing_into_the_runs_outputs_is_reused_but_not_one_writing_outside_the_run(tmp_path):
    # email-summaries writes `${output_file}`, outputs/summaries.json in the run (run 2026-10-16-2 judged again: only
    # work/ counted); a path outside the run is overwritten by every run, so it has nothing to copy from
    (tmp_path / "mail").mkdir()
    (tmp_path / "mail" / "a.eml").write_text("Subject: hi\n\nhello")

    def compile_with(rid, where):
        run_dir = tmp_path / "runs" / rid
        run_dir.mkdir(parents=True, exist_ok=True)
        stage = {**MAP_STAGES[0], "writes": [{"id": "summaries", "path": str(where(run_dir))}]}
        c = cw.Compiler(tmp_path, {"stages": [stage], "inputs": [{"id": "emails", "kind": "folder", "path": str(tmp_path / "mail")}]},
                        {}, run_dir, {}, set())
        return c, c.compile({"id": "w1", "stages": ["summarise"]}, reuse=True)

    def finish(c, path):
        (c.run_dir / "run.yaml").write_text("status: done\nstages:\n  summarise: done\n")
        cw.write_wave(c.run_dir, "w1", {"inputs": c.fingerprints, "result": {"status": "done", "children": {
            "summarise-merge": {"status": "completed"}}}})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"summaries of {c.run_dir.name}")

    in_run = lambda run_dir: run_dir / "outputs" / "summaries.json"
    c, _ = compile_with("2026-01-01-1", in_run)
    finish(c, in_run(c.run_dir))
    c, again = compile_with("2026-01-01-2", in_run)
    assert items(again["spec"])["summarise"]["reuse"]["run"] == "2026-01-01-1"
    c.copy_reused()
    assert in_run(c.run_dir).read_text() == "summaries of 2026-01-01-1"

    outside = lambda run_dir: tmp_path / "desktop" / "summaries.json"
    c, _ = compile_with("2026-01-02-1", outside)
    assert "summarise" not in c.fingerprints
    finish(c, outside(c.run_dir))
    _, again = compile_with("2026-01-02-2", outside)
    assert any("map" in step for step in again["spec"]["steps"])


def map_of(result, key="summarise"):
    """The map step for `key`, with its split's request and its merge's, or None."""
    m = next((step["map"] for step in result["spec"]["steps"] if step.get("map", {}).get("key") == key), None)
    return m and {**m, "split_req": json.loads(m["split"]["task"].removeprefix(cw.BATCH_REQUEST))}


def test_a_map_stage_maps_only_the_items_no_earlier_run_mapped(tmp_path):
    # TASKS 38: one email more is not a reason to map them all again. The split gets the item stores of earlier runs
    # that finished the stage with the same item fingerprint (the batch size not in it), the merge this run's store
    (tmp_path / "mail").mkdir()
    for n in range(3):
        (tmp_path / "mail" / f"{n}.eml").write_text(f"Subject: {n}\n\nbody {n}")
    c, first = compile_map_run(tmp_path, "2026-01-01-1")
    m = map_of(first)
    assert m["split_req"]["stores"] == [] and m["merge"]["store"] == str(c.work / "summarise" / "items")
    assert m["split_req"]["items_file"] == m["merge"]["items_file"] and "summarise/items" in c.fingerprints
    finish_map(c, "2026-01-01-1")
    (c.work / "summarise" / "items").mkdir(parents=True)
    (c.work / "summarise" / "items" / "k.json").write_text('{"item": "x", "record": "x: ok"}')

    (tmp_path / "mail" / "3.eml").write_text("Subject: new\n\nmore")
    c, grown = compile_map_run(tmp_path, "2026-01-01-2")
    assert map_of(grown)["split_req"]["stores"] == [str(tmp_path / "runs" / "2026-01-01-1" / "work" / "summarise" / "items")]
    assert any("left out of its batches" in n for n in grown["notes"]) and "reuse" not in items(grown["spec"])["digest"]

    _, rebatched = compile_map_run(tmp_path, "2026-01-01-3", batch=3)  # not reused whole, but item by item
    assert len(map_of(rebatched)["split_req"]["stores"]) == 1

    (tmp_path / "mail" / "3.eml").unlink()  # the first run's items again: reused whole, and its store comes along
    c, same = compile_map_run(tmp_path, "2026-01-01-4")
    assert items(same["spec"])["summarise"]["reuse"]["run"] == "2026-01-01-1" and "summarise/items" in c.fingerprints
    c.copy_reused()
    assert (c.work / "summarise" / "items" / "k.json").is_file()


def test_a_map_reduce_stage_is_not_reused_item_by_item(tmp_path):
    (tmp_path / "mail").mkdir()
    (tmp_path / "mail" / "a.eml").write_text("Subject: hi\n\nhello")
    run_dir = tmp_path / "runs" / "2026-01-01-1"
    run_dir.mkdir(parents=True)
    stage = {**MAP_STAGES[0], "pattern": "map_reduce"}
    c = cw.Compiler(tmp_path, {"stages": [stage], "inputs": [{"id": "emails", "kind": "folder", "path": str(tmp_path / "mail")}]},
                    {}, run_dir, {}, set())
    m = map_of(c.compile({"id": "w1", "stages": ["summarise"]}, reuse=True))
    assert "items_file" not in m["split_req"] and "summarise/items" not in c.fingerprints
