"""Validator regression: both examples must pass; each broken variant must
report an error (or warning) containing the expected text.
"""
import copy
import sys

import pytest
from conftest import SKILL

sys.path.insert(0, str(SKILL / "scripts"))
import validate_plan

EX = SKILL / "examples"


def example(name):
    return (EX / name).read_text(encoding="utf-8")


INV = validate_plan.load_yaml(example("invoice-check.yaml"))
COMP = validate_plan.load_yaml(example("competitor-memo.yaml"))


def st(p, key):
    return next(s for s in p["stages"] if s["key"] == key)


def ran(p, **stages):
    """Give the case a run in which these stages are running or done."""
    p["__run__"] = {"id": "test-run", "status": "active", "stages": {k.replace("_", "-"): v for k, v in stages.items()}}


def check(base, mutate):
    p = copy.deepcopy(base)
    mutate(p)
    return validate_plan.validate(p, run_state=p.pop("__run__", None))


# (name, base plan, mutation, expected substring, "error" | "warning")
CASES = [
    ("open question but ready", INV, lambda p: p["questions"][0].__setitem__("answer", None), "are unanswered", "error"),
    ("side effect without approval", INV, lambda p: st(p, "drafts").__setitem__("approval", "none"), "need approval: required", "error"),
    ("waves listed in the plan", INV, lambda p: p.__setitem__("waves", [{"id": "w1", "stages": ["find"]}]), "plans do not list waves", "error"),
    ("status in the plan", INV, lambda p: st(p, "find").__setitem__("status", "done"), "status belongs to the run", "error"),
    ("done stage needs a pending one", INV, lambda p: ran(p, extract="done"), "is done in run test-run but needs 'find', which is pending", "error"),
    ("provisional stage running", COMP, lambda p: ran(p, discover="done", confirm_list="done", analyse="running"), "is provisional but running", "error"),
    ("cycle", INV, lambda p: st(p, "find").__setitem__("needs", ["compare"]), "dependency cycle", "error"),
    ("consumes without needs", INV, lambda p: st(p, "compare").__setitem__("needs", []), "which it does not need", "error"),
    ("judgment data output without schema", INV, lambda p: st(p, "find")["out"][0].pop("schema"), "needs a schema", "error"),
    ("deterministic without verify", INV, lambda p: st(p, "extract").pop("verify"), "need verify", "error"),
    ("map without batch", INV, lambda p: st(p, "find").pop("batch"), "positive integer batch", "error"),
    ("map without items", INV, lambda p: st(p, "find").pop("items"), "need `items`", "error"),
    ("gate on map", INV, lambda p: st(p, "find").__setitem__("verify", {"gate": "true"}), "gates on map", "error"),
    ("unknown agent", INV, lambda p: st(p, "extract").__setitem__("agent", "parser"), "not in the available agents", "error"),
    ("typed gate plus returned schema", INV, lambda p: (st(p, "review")["out"][0].pop("path"), st(p, "review").__setitem__("verify", {"gate": {"command": "x", "output": "json"}})), "typed gate cannot be combined", "error"),
    ("map child key collision", INV, lambda p: p["stages"].append({"key": "find-2", "title": "x", "kind": "deterministic", "pattern": "script", "agent": "worker", "verify": {"check": "x"}}), "collides with child keys", "error"),
    ("absolute out path", INV, lambda p: st(p, "compare")["out"][0].__setitem__("path", "~/report.xlsx"), "path is absolute", "error"),
    ("path param after path text", INV, lambda p: st(p, "compare")["out"][0].__setitem__("path", "../${report_dir}/r.xlsx"), "compiles to an absolute path", "error"),
    ("deterministic work in a worker", INV, lambda p: (st(p, "extract").pop("script"), st(p, "extract").pop("run"), st(p, "extract").__setitem__("pattern", "worker")), "deterministic work in a worker child", "warning"),
    ("side effect on an app with a builtin agent", INV, lambda p: None, "has no connector tools", "warning"),
    ("undefined param", INV, lambda p: st(p, "compare")["out"][0].__setitem__("path", "${nope}/r.xlsx"), "undefined param", "error"),
    ("user-scoped agent", INV, lambda p: (p["agents"]["available"].append({"name": "x-parser", "source": "user"}), st(p, "extract").__setitem__("agent", "x-parser")), "user-scoped", "error"),
    ("asked finding without question", INV, lambda p: p["questions"][2].pop("from_finding"), "marked asked", "error"),
    ("ready without critique", INV, lambda p: (p.pop("critique"), [q.pop("from_finding", None) for q in p["questions"]]), "no critique", "error"),
    ("provisional in next wave", COMP, lambda p: ran(p, discover="done"), "has provisional stages", "error"),
    ("discovery nothing replans on", COMP, lambda p: p["unknowns"][0].__setitem__("stage", "memo"), "nothing replans on this discovery", "error"),
    ("resolves_after cycle", COMP, lambda p: st(p, "analyse").__setitem__("resolves_after", "memo"), "resolves_after makes a cycle", "error"),
    ("provisional without intent", COMP, lambda p: st(p, "analyse").pop("intent"), "need an intent", "error"),
    ("probe unknown unresolved", COMP, lambda p: p["unknowns"].append({"id": "u9", "text": "x", "resolve_by": "probe"}), "no finding", "error"),
    ("memo without reviewer", COMP, lambda p: (p["stages"].remove(st(p, "review-memo")), st(p, "publish").__setitem__("needs", ["memo"]), st(p, "publish").__setitem__("consumes", ["memo_draft"])), "needs verify.review", "error"),
    ("returned result across waves", COMP, lambda p: st(p, "review-memo")["out"][0].pop("path"), "from an earlier wave", "error"),
    ("shape: unknown kind", INV, lambda p: st(p, "extract").__setitem__("kind", "mechanical"), "stage 'extract': kind: Input should be", "error"),
    ("shape: pattern lanes (removed, never compiled)", INV, lambda p: st(p, "review").__setitem__("pattern", "lanes"), "pattern: Input should be", "error"),
    ("shape: provisional not a boolean", INV, lambda p: st(p, "extract").__setitem__("provisional", "yes"), "provisional: Input should be a valid boolean", "error"),
    ("redundant context", INV, lambda p: st(p, "review").__setitem__("context", "fresh"), "already the default", "warning"),
    ("param reads a system variable", INV, lambda p: p["params"]["mail_dir"].__setitem__("env", "HOME"), "the system or a tool already uses", "error"),
    ("param env not a variable name", INV, lambda p: p["params"]["mail_dir"].__setitem__("env", "MAIL-DIR"), "not a valid environment variable name", "error"),
    ("two params read one variable", INV, lambda p: p["params"]["budget_file"].__setitem__("env", "MAIL_DIR"), "like param 'mail_dir'", "error"),
    ("secret with a default", COMP, lambda p: p["params"]["notion_token"].__setitem__("default", "x"), "a secret has no default", "error"),
    ("secret in a path", INV, lambda p: p["params"]["report_dir"].__setitem__("type", "secret") or p["params"]["report_dir"].pop("default"), "cannot be part of a path", "error"),
    ("script with a folder", INV, lambda p: st(p, "extract").__setitem__("script", "../x.py"), "must be a file name", "error"),
    ("script on a judgment stage", INV, lambda p: st(p, "review").__setitem__("script", "r.py"), "a judgment stage has no program", "error"),
    ("script on a map stage", INV, lambda p: (st(p, "find").__setitem__("kind", "deterministic"), st(p, "find").__setitem__("script", "f.py")), "not supported yet", "error"),
    ("run without script", INV, lambda p: st(p, "review").__setitem__("run", "a b"), "name the program with script:", "error"),
    ("run with a returned output", INV, lambda p: (st(p, "extract").__setitem__("run", "x"), st(p, "extract")["out"][1].pop("path")), "writes files and returns nothing", "error"),
    ("run with an undefined param", INV, lambda p: st(p, "extract").__setitem__("run", "${nope}"), "undefined param", "error"),
    ("script without run", INV, lambda p: st(p, "extract").pop("run"), "give `run:` the program's arguments", "warning"),
    ("script with pattern worker", INV, lambda p: st(p, "extract").__setitem__("pattern", "worker"), "has pattern: script", "error"),
    ("two stages share a script", INV, lambda p: st(p, "compare").__setitem__("script", "extract_invoice_lines.py"), "give each stage its own", "error"),
    ("script stage without a program", INV, lambda p: st(p, "extract").pop("script"), "name its program", "warning"),
    ("unknown param type", INV, lambda p: p["params"]["mail_dir"].__setitem__("type", "folder"), "param 'mail_dir': type: Input should be", "error"),
]

# Derived waves: (name, base plan, mutation, expected [(stages, ends_with)] of the waves still to run)
WAVES = [
    ("invoice-check: decision and side effect start a wave", INV, lambda p: None,
     [(["find", "extract", "extract-rest", "compare", "review"], "none"), (["approve-drafts", "drafts"], "approval")]),
    ("competitor-memo: replan after discovery", COMP, lambda p: None,
     [(["discover"], "replan"), (["confirm-list", "analyse", "memo", "review-memo"], "none"), (["publish"], "approval")]),
    ("pause_after moves the stages that need it", INV, lambda p: st(p, "compare").__setitem__("pause_after", True),
     [(["find", "extract", "extract-rest", "compare"], "review"), (["review"], "none"), (["approve-drafts", "drafts"], "approval")]),
    ("done stages leave the waves", INV, lambda p: ran(p, find="done", extract="done", extract_rest="done", compare="done", review="done"),
     [(["approve-drafts", "drafts"], "approval")]),
]


@pytest.mark.parametrize("name,base", [("invoice-check", INV), ("competitor-memo", COMP)])
def test_example_is_valid(name, base):
    errors, _warnings, _summary = validate_plan.validate(copy.deepcopy(base))
    assert not errors


@pytest.mark.parametrize("name,base,mutate,expected,level", CASES, ids=[c[0] for c in CASES])
def test_broken_plan_reports(name, base, mutate, expected, level):
    errors, warnings, _summary = check(base, mutate)
    pool = errors if level == "error" else warnings
    assert any(expected in m for m in pool), f"expected {level} containing {expected!r}, got errors={errors} warnings={warnings}"


@pytest.mark.parametrize("name,base,mutate,expected", WAVES, ids=[w[0] for w in WAVES])
def test_derived_waves(name, base, mutate, expected):
    errors, _warnings, summary = check(base, mutate)
    got = [(w["stages"], w["ends_with"]) for w in summary.get("waves") or []]
    assert not errors
    assert got == expected


def test_repeated_key_after_a_dropped_list_item():
    # Text-level case: an edit that drops a `- id:` line merges two list items;
    # a plain YAML loader would silently keep the second item's values.
    text = example("invoice-check.yaml").replace("  - id: q2\n", "", 1)
    with pytest.raises(validate_plan.YAMLError, match="duplicate key"):
        validate_plan.load_yaml(text)


def test_project_agent_listed_as_builtin(tmp_path):
    # A plain name means source: builtin; an agent the agent defines should say source: project.
    (tmp_path / ".pi" / "agents").mkdir(parents=True)
    (tmp_path / ".pi" / "agents" / "worker.md").write_text("---\nname: worker\n---\nbody\n", encoding="utf-8")
    _errors, warnings, _summary = validate_plan.validate(copy.deepcopy(INV), agent_root=tmp_path)
    assert any("'worker' is defined in .pi/agents/ but listed as builtin" in w for w in warnings), warnings


def test_gate_packages_come_from_the_agents_pyproject(tmp_path):
    # one list of packages per agent; `uv run` syncs it before running a gate
    (tmp_path / "gates").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "w"\ndependencies = ["openpyxl>=3.1", "PyYAML"]\n',
                                              encoding="utf-8")
    (tmp_path / "gates" / "check_lines.py").write_text("import csv\nimport not_listed_pkg\n", encoding="utf-8")
    (tmp_path / "gates" / "check_report.py").write_text("import openpyxl\nimport yaml\n", encoding="utf-8")
    plan = copy.deepcopy(INV)
    st(plan, "compare")["verify"]["gate"] = "uv run ../gates/check_report.py ${report_dir}/Q3-invoice-variance.xlsx"
    errors, _warnings, _summary = validate_plan.validate(plan, agent_root=tmp_path)
    assert any("check_lines.py would fail" in e and "agent_add_package" in e and "not_listed_pkg" in e for e in errors), errors
    assert not any("check_report.py would fail" in e for e in errors), errors  # yaml is PyYAML's import name


def test_gate_with_packages_must_run_with_uv_and_not_declare_its_own(tmp_path):
    (tmp_path / "gates").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "w"\ndependencies = ["openpyxl"]\n', encoding="utf-8")
    (tmp_path / "gates" / "check_lines.py").write_text('# /// script\n# dependencies = ["openpyxl"]\n# ///\nimport openpyxl\n',
                                                         encoding="utf-8")
    (tmp_path / "gates" / "check_report.py").write_text("import openpyxl\n", encoding="utf-8")  # run with python3
    errors, _warnings, _summary = validate_plan.validate(copy.deepcopy(INV), agent_root=tmp_path)
    assert any("check_lines.py would fail" in e and "pyproject.toml instead" in e for e in errors), errors
    assert any("check_report.py would fail" in e and "`uv run`" in e for e in errors), errors


def test_a_side_effect_agent_must_reach_its_target(tmp_path):
    # Gmail drafts need a connector (mcp:) or a CLI (bash); an agent with neither cannot make them
    (tmp_path / ".pi" / "agents").mkdir(parents=True)
    plan = copy.deepcopy(INV)
    plan["agents"]["available"].append({"name": "drafter", "source": "project"})
    st(plan, "drafts")["agent"] = "drafter"
    agent = tmp_path / ".pi" / "agents" / "drafter.md"
    agent.write_text("---\nname: drafter\ntools: read, write\n---\nbody\n", encoding="utf-8")
    errors, _warnings, _summary = validate_plan.validate(plan, agent_root=tmp_path)
    assert any("'drafter' cannot reach Gmail drafts" in e for e in errors), errors
    agent.write_text("---\nname: drafter\ntools: [read, mcp:gmail/create_draft]\n---\nbody\n", encoding="utf-8")
    errors, warnings, _summary = validate_plan.validate(plan, agent_root=tmp_path)
    assert not any("cannot reach" in e for e in errors) and not any("connector" in w for w in warnings), (errors, warnings)
