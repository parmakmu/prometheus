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
