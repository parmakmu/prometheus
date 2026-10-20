"""run_batcher.py: split a folder into batches, merge JSON batch outputs."""
import json
import sys

from conftest import SKILL


def test_split_seven_items_into_three_batches(run_py, tmp_path):
    mail = tmp_path / "mail"
    mail.mkdir()
    for i in range(1, 8):
        (mail / f"m{i}.eml").write_text(f"m{i}", encoding="utf-8")
    req = {"op": "split", "source": str(mail), "glob": "*.eml", "batch": 3, "outdir": str(tmp_path / "b")}
    r = run_py([SKILL / "scripts" / "run_batcher.py", json.dumps(req)])
    assert r.data is not None, r
    assert len(r.data["batches"]) == 3


def test_merge_json(run_py, tmp_path):
    (tmp_path / "a.json").write_text("[1]", encoding="utf-8")
    (tmp_path / "b.json").write_text("[2,3]", encoding="utf-8")
    req = {"op": "merge", "inputs": [str(tmp_path / "a.json"), str(tmp_path / "b.json")], "output": str(tmp_path / "m.json")}
    r = run_py([SKILL / "scripts" / "run_batcher.py", json.dumps(req)])
    assert r.data is not None, r
    assert r.data["count"] == 3


def merge_with_items(run_py, tmp_path, results, ext=".json"):
    """Split four emails into two batches, write `results` (one per batch) and merge them, checking the items."""
    mail = tmp_path / "mail"
    mail.mkdir()
    for i in range(1, 5):
        (mail / f"m{i}.eml").write_text(f"m{i}", encoding="utf-8")
    split = run_py([SKILL / "scripts" / "run_batcher.py", json.dumps(
        {"op": "split", "source": str(mail), "glob": "*.eml", "batch": 2, "outdir": str(tmp_path / "b")})]).data
    outs = []
    for i, result in enumerate(results):
        out = tmp_path / f"r{i}{ext}"
        out.write_text(result if isinstance(result, str) else json.dumps(result), encoding="utf-8")
        outs.append(str(out))
    req = {"op": "merge", "inputs": outs, "output": str(tmp_path / f"m{ext}"), "batches": [b["file"] for b in split["batches"]]}
    return run_py([SKILL / "scripts" / "run_batcher.py", json.dumps(req)])


def test_a_merge_with_every_item_once_passes_whether_named_by_path_or_file_name(run_py, tmp_path):
    r = merge_with_items(run_py, tmp_path, [[{"file": "m1.eml"}, {"file": str(tmp_path / "mail" / "m2.eml")}],
                                            [{"file": "mail/m3.eml"}, {"meta": {"file": "m4.eml"}}]])
    assert r.returncode == 0 and r.data["items"] == {"checked": 4, "unnamed": 0}, r


def test_a_merge_missing_an_item_or_naming_one_twice_fails_and_names_them(run_py, tmp_path):
    r = merge_with_items(run_py, tmp_path, [[{"file": "m1.eml"}, {"file": "m1.eml"}], [{"file": "m3.eml"}]])
    assert r.returncode == 1 and "missing: m2.eml, m4.eml" in r.stderr and "named more than once: m1.eml" in r.stderr, r


def test_a_text_merge_checks_items_as_whole_words(run_py, tmp_path):
    r = merge_with_items(run_py, tmp_path, ["m1.eml: ok\nm2.eml: ok\n", "m3.eml: ok\nxm4.eml: ok\n"], ext=".txt")
    assert r.returncode == 1 and "missing: m4.eml" in r.stderr, r  # "xm4.eml" is another item's name


def test_items_without_a_name_are_counted_not_checked(run_py, tmp_path):
    (tmp_path / "items.json").write_text(json.dumps([{"a": 1}, {"a": 2}]), encoding="utf-8")
    split = run_py([SKILL / "scripts" / "run_batcher.py", json.dumps(
        {"op": "split", "source": str(tmp_path / "items.json"), "batch": 1, "outdir": str(tmp_path / "b")})]).data
    (tmp_path / "r.json").write_text("[]", encoding="utf-8")
    req = {"op": "merge", "inputs": [str(tmp_path / "r.json")], "output": str(tmp_path / "m.json"),
           "batches": [b["file"] for b in split["batches"]]}
    r = run_py([SKILL / "scripts" / "run_batcher.py", json.dumps(req)])
    assert r.returncode == 0 and r.data["items"] == {"checked": 0, "unnamed": 2}, r


def run_op(run_py, tmp_path, command, gate=None, gate_json=False):
    req = {"op": "run", "command": command, "gate": gate, "gate_json": gate_json}
    return run_py([SKILL / "scripts" / "run_batcher.py", json.dumps(req)], cwd=tmp_path)


def test_run_executes_the_program_then_its_gate(run_py, tmp_path):
    r = run_op(run_py, tmp_path, "echo made > out.txt", gate="test -f out.txt && echo '{\"ok\": true}'", gate_json=True)
    assert r.returncode == 0, r
    assert r.data["gate"] == {"ok": True} and (tmp_path / "out.txt").is_file()


def test_run_reports_a_failing_program_on_stderr(run_py, tmp_path):
    r = run_op(run_py, tmp_path, "echo 'Traceback: bad header' >&2; exit 3", gate="true")
    assert r.returncode == 1 and "exit code 3" in r.stderr and "bad header" in r.stderr


def test_run_reports_a_failing_gate(run_py, tmp_path):
    r = run_op(run_py, tmp_path, "true", gate="echo 'row count 7, expected 8' >&2; exit 1")
    assert r.returncode == 1 and "gate failed" in r.stderr and "expected 8" in r.stderr


def test_a_program_lacking_a_package_reports_it_for_the_parent(run_py, tmp_path):
    # no model can add a package: the batcher writes the stage's report and marks the failure (no fix child)
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "convert.py").write_text("import markitdown_not_installed_xyz.cli\n", encoding="utf-8")
    (tmp_path / "scripts" / "local_bug.py").write_text("import helpers.missing_sub\n", encoding="utf-8")  # its own module
    (tmp_path / "scripts" / "helpers").mkdir()
    (tmp_path / "scripts" / "stdlib.py").write_text("import tomllib.nope\n", encoding="utf-8")
    report = tmp_path / "work" / ".missing" / "convert.json"

    def run(program):
        req = {"op": "run", "command": f"{sys.executable} scripts/{program}.py", "missing": str(report)}
        return run_py([SKILL / "scripts" / "run_batcher.py", json.dumps(req)], cwd=tmp_path)

    r = run("convert")
    assert r.returncode == 1 and "MISSING_REPORTED" in r.stderr and "'markitdown_not_installed_xyz'" in r.stderr, r
    assert json.loads(report.read_text())["kind"] == "package"
    assert json.loads(report.read_text())["need"] == "markitdown_not_installed_xyz"
    report.unlink()
    for program in ("local_bug", "stdlib"):  # a bug for the fix child, not a package to add
        r = run(program)
        assert r.returncode == 1 and "MISSING_REPORTED" not in r.stderr and "No module named" in r.stderr, (program, r)
        assert not report.exists(), program


def test_run_fails_a_program_that_hangs(run_py, tmp_path):
    req = {"op": "run", "command": "sleep 5", "timeout": 1}
    r = run_py([SKILL / "scripts" / "run_batcher.py", json.dumps(req)], cwd=tmp_path)
    assert r.returncode == 1 and "no result within 1 s" in r.stderr


def test_merge_runs_its_gate_on_the_merged_output(run_py, tmp_path):
    (tmp_path / "a.json").write_text("[1]", encoding="utf-8")
    req = {"op": "merge", "inputs": [str(tmp_path / "a.json")], "output": str(tmp_path / "m.json"),
           "gate": "grep -q 2 m.json"}
    r = run_py([SKILL / "scripts" / "run_batcher.py", json.dumps(req)], cwd=tmp_path)
    assert r.returncode == 1 and "gate failed" in r.stderr


def test_a_command_gets_only_the_secrets_it_names(run_py, tmp_path, monkeypatch):
    # pi's environment holds no secrets: the batcher adds those a command names, from .env, to that command only
    monkeypatch.delenv("API_TOKEN", raising=False)
    (tmp_path / ".env").write_text("API_TOKEN='s3cret'\nOTHER=\"nope\"\n", encoding="utf-8")
    req = {"op": "run", "command": 'echo "tok=${API_TOKEN} other=${OTHER}"', "secrets": ["API_TOKEN"], "env_file": str(tmp_path / ".env")}
    r = run_py([SKILL / "scripts" / "run_batcher.py", json.dumps(req)], cwd=tmp_path)
    assert r.data["program"]["stdout"].strip() == "tok=s3cret other=", r


def batcher(run_py, req, cwd=None):
    return run_py([SKILL / "scripts" / "run_batcher.py", json.dumps(req)], cwd=cwd)


def test_items_an_earlier_merge_stored_are_left_out_and_merged_back_in_item_order(run_py, tmp_path):
    # TASKS 38: per-item reuse. Run 1 maps three emails and stores each one's line; run 2's folder has moved, m2
    # changed and m4 is new: only those two are mapped, and m1's and m3's lines come back, renamed to the new folder
    one, two = tmp_path / "one", tmp_path / "two"
    one.mkdir()
    for i in (1, 2, 3):
        (one / f"m{i}.eml").write_text(f"m{i}", encoding="utf-8")
    split = batcher(run_py, {"op": "split", "source": str(one), "batch": 2, "outdir": str(tmp_path / "b1"),
                             "items_file": str(tmp_path / "items1.json"), "stores": []}).data
    assert (split["total"], split["known"], len(split["batches"])) == (3, 0, 2)
    (tmp_path / "r0.txt").write_text(f"{one}/m2.eml: two\n{one}/m1.eml: one\n", encoding="utf-8")  # any order in a batch
    (tmp_path / "r1.txt").write_text(f"{one}/m3.eml: three\n", encoding="utf-8")
    merged = batcher(run_py, {"op": "merge", "inputs": [str(tmp_path / "r0.txt"), str(tmp_path / "r1.txt")],
                              "output": str(tmp_path / "m1.txt"), "items_file": str(tmp_path / "items1.json"),
                              "store": str(tmp_path / "store1")})
    assert merged.returncode == 0 and merged.data["items"] == {"checked": 3, "unnamed": 0, "reused": 0, "stored": 3}, merged
    assert (tmp_path / "m1.txt").read_text().splitlines() == [f"{one}/m1.eml: one", f"{one}/m2.eml: two", f"{one}/m3.eml: three"]
    assert len(list((tmp_path / "store1").glob("*.json"))) == 3

    two.mkdir()
    for i, text in ((1, "m1"), (2, "m2 changed"), (3, "m3"), (4, "m4")):
        (two / f"m{i}.eml").write_text(text, encoding="utf-8")
    split = batcher(run_py, {"op": "split", "source": str(two), "batch": 2, "outdir": str(tmp_path / "b2"),
                             "items_file": str(tmp_path / "items2.json"), "stores": [str(tmp_path / "store1")]}).data
    assert (split["total"], split["known"], len(split["batches"])) == (4, 2, 1)
    assert (tmp_path / "b2" / "0.txt").read_text().splitlines() == [str(two / "m2.eml"), str(two / "m4.eml")]
    (tmp_path / "s0.txt").write_text(f"{two}/m4.eml: four\n{two}/m2.eml: two again\n", encoding="utf-8")
    merged = batcher(run_py, {"op": "merge", "inputs": [str(tmp_path / "s0.txt")], "output": str(tmp_path / "m2.txt"),
                              "items_file": str(tmp_path / "items2.json"), "store": str(tmp_path / "store2")})
    assert merged.returncode == 0 and merged.data["items"] == {"checked": 4, "unnamed": 0, "reused": 2, "stored": 4}, merged
    assert (tmp_path / "m2.txt").read_text().splitlines() == [
        f"{two}/m1.eml: one", f"{two}/m2.eml: two again", f"{two}/m3.eml: three", f"{two}/m4.eml: four"]
    assert len(list((tmp_path / "store2").glob("*.json"))) == 4  # the reused ones too: the newest store is whole


def test_a_merge_whose_gate_fails_stores_nothing(run_py, tmp_path):
    (tmp_path / "list.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    batcher(run_py, {"op": "split", "source": str(tmp_path / "list.txt"), "batch": 5, "outdir": str(tmp_path / "b"),
                     "items_file": str(tmp_path / "items.json"), "stores": []})
    (tmp_path / "r.json").write_text(json.dumps([{"item": "beta", "ok": 1}, {"item": "alpha", "ok": 1}, {"total": 2}]))
    req = {"op": "merge", "inputs": [str(tmp_path / "r.json")], "output": str(tmp_path / "m.json"),
           "items_file": str(tmp_path / "items.json"), "store": str(tmp_path / "store"), "gate": "false"}
    assert batcher(run_py, req, cwd=tmp_path).returncode == 1 and not (tmp_path / "store").exists()
    r = batcher(run_py, {**req, "gate": None}, cwd=tmp_path)
    assert r.returncode == 0 and r.data["items"]["stored"] == 2, r  # a record naming no item is not stored, and goes last
    assert [x.get("item") for x in json.loads((tmp_path / "m.json").read_text())] == ["alpha", "beta", None]


def test_items_without_a_name_are_never_left_out(run_py, tmp_path):
    (tmp_path / "items.json").write_text(json.dumps([{"a": 1}]), encoding="utf-8")
    (tmp_path / "store").mkdir()
    split = batcher(run_py, {"op": "split", "source": str(tmp_path / "items.json"), "batch": 1, "outdir": str(tmp_path / "b"),
                             "items_file": str(tmp_path / "list.json"), "stores": [str(tmp_path / "store")]}).data
    assert (split["known"], len(split["batches"])) == (0, 1)
    assert json.loads((tmp_path / "list.json").read_text())["named"] is False
