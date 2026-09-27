"""batch_tool.py: split a folder into batches, merge JSON batch outputs."""
import json

from conftest import SKILL


def test_split_seven_items_into_three_batches(run_py, tmp_path):
    mail = tmp_path / "mail"
    mail.mkdir()
    for i in range(1, 8):
        (mail / f"m{i}.eml").write_text(f"m{i}", encoding="utf-8")
    req = {"op": "split", "source": str(mail), "glob": "*.eml", "batch": 3, "outdir": str(tmp_path / "b")}
    r = run_py([SKILL / "scripts" / "batch_tool.py", json.dumps(req)])
    assert r.data is not None, r
    assert len(r.data["batches"]) == 3


def test_merge_json(run_py, tmp_path):
    (tmp_path / "a.json").write_text("[1]", encoding="utf-8")
    (tmp_path / "b.json").write_text("[2,3]", encoding="utf-8")
    req = {"op": "merge", "inputs": [str(tmp_path / "a.json"), str(tmp_path / "b.json")], "output": str(tmp_path / "m.json")}
    r = run_py([SKILL / "scripts" / "batch_tool.py", json.dumps(req)])
    assert r.data is not None, r
    assert r.data["count"] == 3


def run_op(run_py, tmp_path, command, gate=None, gate_json=False):
    req = {"op": "run", "command": command, "gate": gate, "gate_json": gate_json}
    return run_py([SKILL / "scripts" / "batch_tool.py", json.dumps(req)], cwd=tmp_path)


def test_run_executes_the_program_then_its_gate(run_py, tmp_path):
    r = run_op(run_py, tmp_path, "echo made > out.txt", gate="test -f out.txt && echo '{\"ok\": true}'", gate_json=True)
    assert r.returncode == 0, r
    assert r.data["gate"] == {"ok": True} and (tmp_path / "out.txt").is_file()


def test_run_reports_a_failing_program_on_stderr(run_py, tmp_path):
    r = run_op(run_py, tmp_path, "echo 'Traceback: bad header' >&2; exit 3", gate="true")
    assert r.returncode == 1 and "exited with code 3" in r.stderr and "bad header" in r.stderr


def test_run_reports_a_failing_gate(run_py, tmp_path):
    r = run_op(run_py, tmp_path, "true", gate="echo 'row count 7, expected 8' >&2; exit 1")
    assert r.returncode == 1 and "gate failed" in r.stderr and "expected 8" in r.stderr
