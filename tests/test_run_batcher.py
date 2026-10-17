"""run_batcher.py: split a folder into batches, merge JSON batch outputs."""
import json

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
