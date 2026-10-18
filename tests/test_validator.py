"""Validator regression: both examples must pass; each broken variant must
report an error (or warning) containing the expected text.
"""
import copy
import sys

import pytest
from conftest import SKILL

sys.path.insert(0, str(SKILL / "scripts"))
import plan_validate

EX = SKILL / "examples"


def example(name):
    return (EX / name).read_text(encoding="utf-8")


INV = plan_validate.read_plan(EX / "invoice-check", strict=True)  # plan.yaml with its .plan/
COMP = plan_validate.read_plan(EX / "competitor-memo", strict=True)


def st(p, key):
    return next(s for s in p["stages"] if s["id"] == key)


def ran(p, **stages):
    """Give the case a run in which these stages are running or done."""
    p["__run__"] = {"id": "test-run", "status": "active", "stages": {k.replace("_", "-"): v for k, v in stages.items()}}


def check(base, mutate):
    p = copy.deepcopy(base)
    mutate(p)
    run_state = p.pop("__run__", None)
    if isinstance(p.get("critique"), dict):  # the critic's record follows the change: these cases test other rules
        p["critique"]["plan_digest"] = plan_validate.plan_digest(p)
    return plan_validate.validate(p, run_state=run_state)


# (name, base plan, mutation, expected substring, "error" | "warning")
CASES = [
    ("open question but ready", INV, lambda p: p["questions"][0].__setitem__("answer", None), "are unanswered", "error"),
    ("kind as a bare word", INV, lambda p: st(p, "extract").__setitem__("kind", "deterministic"), "kind is a mapping: {value: deterministic", "error"),
    ("an unknown stage field", INV, lambda p: st(p, "compare").__setitem__("consumes", ["budget"]), "unknown field 'consumes'", "error"),
    ("waves listed in the plan", INV, lambda p: p.__setitem__("waves", [{"id": "w1", "stages": ["find"]}]), "plans do not list waves", "error"),
    ("status in the plan", INV, lambda p: st(p, "find").__setitem__("status", "done"), "status belongs to the run", "error"),
    ("done stage needs a pending one", INV, lambda p: ran(p, extract="done"), "is done in run test-run but runs after 'find', which is pending", "error"),
    ("provisional stage running", COMP, lambda p: ran(p, discover="done", confirm_list="done", analyse="running"), "is provisional but running", "error"),
    ("cycle", INV, lambda p: st(p, "find").__setitem__("after", ["compare"]), "dependency cycle", "error"),
    ("reads without after", INV, lambda p: st(p, "compare").__setitem__("after", []), "which it does not run after", "error"),
    ("judgment data output without schema", INV, lambda p: st(p, "find")["writes"][0].pop("schema"), "needs a schema", "error"),
    ("deterministic without a check", INV, lambda p: (st(p, "extract").pop("gate"), st(p, "extract").pop("done_when")), "need a gate, review or done_when", "error"),
    ("judgment without a reason", INV, lambda p: st(p, "find")["kind"].pop("reason"), "has no kind.reason", "warning"),
    ("map without batch", INV, lambda p: st(p, "find")["map"].pop("batch"), "positive integer map.batch", "error"),
    ("map without over", INV, lambda p: st(p, "find")["map"].pop("over"), "need `map.over`", "error"),
    ("model on external runner", INV, lambda p: (p.setdefault("agents", {}).setdefault("available", []).append({"name": "cli", "runner": "external-cli"}), st(p, "extract").update(agent="cli", model="m")), "runs no model", "warning"),
    ("unknown model role", INV, lambda p: p.__setitem__("models", {"critic": "m"}), "not a known role", "warning"),
    ("a model stage without instructions", INV, lambda p: st(p, "review").pop("instructions", None), "give it `instructions`", "warning"),
    ("secret in a model stage's gate", INV, lambda p: (p.setdefault("params", {}).__setitem__("tok", {"description": "d", "type": "secret"}), st(p, "review").__setitem__("gate", "check ${tok}")), "reaches only what the batcher runs", "error"),
    ("reads outside content with a shell", INV, lambda p: st(p, "find").__setitem__("agent", "worker"), "reads outside content (mail) as 'worker', which has a shell", "warning"),
    ("network host that isn't one", INV, lambda p: p.__setitem__("network", ["https://api.github.com/x"]), "network", "error"),
    ("non-positive budget", INV, lambda p: p.setdefault("limits", {}).__setitem__("max_cost_usd", 0), "max_cost_usd", "error"),
    ("unknown agent", INV, lambda p: st(p, "extract").__setitem__("agent", "parser"), "not in the available agents", "error"),
    ("typed gate plus returned schema", INV, lambda p: (st(p, "review")["writes"][0].pop("path"), st(p, "review").__setitem__("gate", {"command": "x", "output": "json"})), "typed gate cannot be combined", "error"),
    ("map child key collision", INV, lambda p: p["stages"].append({"id": "find-2", "title": "x", "kind": {"value": "deterministic"}, "pattern": "script", "agent": "worker", "done_when": "x"}), "collides with child keys", "error"),
    ("absolute writes path", INV, lambda p: st(p, "compare")["writes"][0].__setitem__("path", "~/report.xlsx"), "to an absolute path", "error"),
    ("path param after path text", INV, lambda p: st(p, "compare")["writes"][0].__setitem__("path", "../${report_dir}/r.xlsx"), "compiles to an absolute path", "error"),
    ("deterministic work in a worker", INV, lambda p: (st(p, "extract").pop("program"), st(p, "extract").__setitem__("pattern", "worker")), "deterministic work in a worker child", "warning"),
    ("side effect on an app with a builtin agent", INV, lambda p: None, "has no connector tools", "warning"),
    ("undefined param", INV, lambda p: st(p, "compare")["writes"][0].__setitem__("path", "${nope}/r.xlsx"), "undefined param", "error"),
    ("user-scoped agent", INV, lambda p: (p["agents"]["available"].append({"name": "x-parser", "source": "user"}), st(p, "extract").__setitem__("agent", "x-parser")), "user-scoped", "error"),
    ("asked finding without question", INV, lambda p: p["questions"][2].pop("from_finding"), "marked asked", "error"),
    ("ready without critique", INV, lambda p: (p.pop("critique"), [q.pop("from_finding", None) for q in p["questions"]]), "no critique", "error"),
    ("provisional in next wave", COMP, lambda p: ran(p, discover="done"), "has provisional stages", "error"),
    ("discovery nothing resolves on", COMP, lambda p: p["unknowns"][0].__setitem__("stage", "memo"), "nothing resolves on this discovery", "error"),
    ("resolves_after cycle", COMP, lambda p: st(p, "analyse").__setitem__("resolves_after", "memo"), "resolves_after makes a cycle", "error"),
    ("resolves_after unknown stage", COMP, lambda p: st(p, "analyse").__setitem__("resolves_after", "nope"), "resolves_after names unknown stage", "error"),
    ("provisional without intent", COMP, lambda p: st(p, "analyse").pop("intent"), "need an intent", "error"),
    ("probe unknown unresolved", COMP, lambda p: p["unknowns"].append({"id": "u9", "text": "x", "resolved_by": "probe"}), "no finding", "error"),
    ("memo without reviewer", COMP, lambda p: (p["stages"].remove(st(p, "review-memo")), st(p, "publish").__setitem__("after", ["memo"]), st(p, "publish").__setitem__("reads", ["memo_draft"])), "needs review or a downstream reviewer", "error"),
    ("returned result across waves", COMP, lambda p: st(p, "review-memo")["writes"][0].pop("path"), "from an earlier wave", "error"),
    ("shape: unknown kind", INV, lambda p: st(p, "extract")["kind"].__setitem__("value", "mechanical"), "stage 'extract': kind.value: Input should be", "error"),
    ("shape: pattern lanes (removed, never compiled)", INV, lambda p: st(p, "review").__setitem__("pattern", "lanes"), "pattern: Input should be", "error"),
    ("shape: resolves_after not a stage id", INV, lambda p: st(p, "extract").__setitem__("resolves_after", "Not A Stage"), "resolves_after: String should match pattern", "error"),
    ("shape: the old provisional flag", INV, lambda p: st(p, "extract").__setitem__("provisional", True), "unknown field 'provisional'", "error"),
    ("redundant context", INV, lambda p: st(p, "review").__setitem__("context", "fresh"), "already the default", "warning"),
    ("param reads a system variable", INV, lambda p: p["params"]["mail_dir"].__setitem__("env", "HOME"), "the system or a tool already uses", "error"),
    ("param env not a variable name", INV, lambda p: p["params"]["mail_dir"].__setitem__("env", "MAIL-DIR"), "not a valid environment variable name", "error"),
    ("two params read one variable", INV, lambda p: p["params"]["budget_file"].__setitem__("env", "MAIL_DIR"), "like param 'mail_dir'", "error"),
    ("secret with a default", COMP, lambda p: p["params"]["notion_token"].__setitem__("default", "x"), "a secret has no default", "error"),
    ("secret per run", COMP, lambda p: p["params"]["notion_token"].__setitem__("per_run", True), "a secret can't be per_run", "error"),
    ("secret in a path", INV, lambda p: p["params"]["report_dir"].__setitem__("type", "secret") or p["params"]["report_dir"].pop("default"), "cannot be part of a path", "error"),
    ("program with a folder", INV, lambda p: st(p, "extract")["program"].__setitem__("file", "../x.py"), "must be a file name", "error"),
    ("program on a judgment stage", INV, lambda p: st(p, "review").__setitem__("program", {"file": "r.py"}), "a judgment stage has none", "error"),
    ("program on a map stage", INV, lambda p: (st(p, "find")["kind"].__setitem__("value", "deterministic"), st(p, "find").__setitem__("program", {"file": "f.py"})), "not supported yet", "error"),
    ("program args without a file", INV, lambda p: st(p, "review").__setitem__("program", {"args": "a b"}), "program.file: Field required", "error"),
    ("program args with a returned output", INV, lambda p: st(p, "extract")["writes"][1].pop("path"), "writes files and returns nothing", "error"),
    ("program args with an undefined param", INV, lambda p: st(p, "extract")["program"].__setitem__("args", "${nope}"), "undefined param", "error"),
    ("program without args", INV, lambda p: st(p, "extract")["program"].pop("args"), "give the program its `args`", "warning"),
    ("program with pattern worker", INV, lambda p: st(p, "extract").__setitem__("pattern", "worker"), "has pattern: script", "error"),
    ("two stages share a program", INV, lambda p: st(p, "compare")["program"].__setitem__("file", "extract_invoice_lines.py"), "give each stage its own", "error"),
    ("script stage without a program", INV, lambda p: st(p, "extract").pop("program"), "name its program", "warning"),
    ("unknown param type", INV, lambda p: p["params"]["mail_dir"].__setitem__("type", "folder"), "param 'mail_dir': type: Input should be", "error"),
]

# Derived waves: (name, base plan, mutation, expected [(stages, ends_with)] of the waves still to run)
WAVES = [
    ("invoice-check: decision and side effect start a wave", INV, lambda p: None,
     [(["find", "extract", "extract-rest", "compare", "review"], "none"), (["approve-drafts", "drafts"], "approval")]),
    ("competitor-memo: resolve after discovery", COMP, lambda p: None,
     [(["discover"], "resolve"), (["confirm-list", "analyse", "memo", "review-memo"], "none"), (["publish"], "approval")]),
    ("pauses_after moves the stages that run after it", INV, lambda p: st(p, "compare").__setitem__("pauses_after", True),
     [(["find", "extract", "extract-rest", "compare"], "review"), (["review"], "none"), (["approve-drafts", "drafts"], "approval")]),
    ("done stages leave the waves", INV, lambda p: ran(p, find="done", extract="done", extract_rest="done", compare="done", review="done"),
     [(["approve-drafts", "drafts"], "approval")]),
]


@pytest.mark.parametrize("name,base", [("invoice-check", INV), ("competitor-memo", COMP)])
def test_example_is_valid(name, base):
    errors, _warnings, _summary = plan_validate.validate(copy.deepcopy(base))
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


def test_a_ready_plan_needs_its_critique_recorded_by_the_tool():
    # agent_record_critique writes plan_digest; a critique written by hand (none), or a plan edited after it, is
    # not ready. A small plan that needs no critic needs no record either.
    hand = copy.deepcopy(INV)
    del hand["critique"]["plan_digest"]
    assert any("not recorded with agent_record_critique" in e for e in plan_validate.validate(hand)[0])
    edited = copy.deepcopy(INV)
    edited["goal"] = "something else"
    assert any("changed after the critic" in e for e in plan_validate.validate(edited)[0])
    assert plan_validate.check_ready({"status": "ready", "stages": [{"id": "a", "kind": {"value": "deterministic"}}]}) is None


def test_the_examples_carry_the_fingerprint_of_their_own_plan():
    # they show the model a ready plan: one edited without restamping would teach a stale record
    for plan in (INV, COMP):
        assert plan["critique"]["plan_digest"] == plan_validate.plan_digest(plan)


def test_repeated_key_after_a_dropped_list_item():
    # Text-level case: an edit that drops a `- id:` line merges two list items;
    # a plain YAML loader would silently keep the second item's values.
    text = example("invoice-check/.plan/questions.yaml").replace("  - id: q2\n", "", 1)
    with pytest.raises(plan_validate.YAMLError, match="duplicate key"):
        plan_validate.load_yaml(text)


def test_project_agent_listed_as_builtin(tmp_path):
    # A plain name means source: builtin; an agent the agent defines should say source: project.
    (tmp_path / ".pi" / "agents").mkdir(parents=True)
    (tmp_path / ".pi" / "agents" / "worker.md").write_text("---\nname: worker\n---\nbody\n", encoding="utf-8")
    _errors, warnings, _summary = plan_validate.validate(copy.deepcopy(INV), agent_root=tmp_path)
    assert any("'worker' is defined in .pi/agents/ but listed as builtin" in w for w in warnings), warnings


def test_gate_packages_come_from_the_agents_pyproject(tmp_path):
    # one list of packages per agent; `uv run` syncs it before running a gate
    (tmp_path / "gates").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "w"\ndependencies = ["openpyxl>=3.1", "PyYAML"]\n',
                                              encoding="utf-8")
    (tmp_path / "gates" / "check_lines.py").write_text("import csv\nimport not_listed_pkg\n", encoding="utf-8")
    (tmp_path / "gates" / "check_report.py").write_text("import openpyxl\nimport yaml\n", encoding="utf-8")
    plan = copy.deepcopy(INV)
    st(plan, "compare")["gate"] = "uv run ../gates/check_report.py ${report_dir}/Q3-invoice-variance.xlsx"
    errors, _warnings, _summary = plan_validate.validate(plan, agent_root=tmp_path)
    assert any("check_lines.py would fail" in e and "agent_add_package" in e and "not_listed_pkg" in e for e in errors), errors
    assert not any("check_report.py would fail" in e for e in errors), errors  # yaml is PyYAML's import name


def test_gate_with_packages_must_run_with_uv_and_not_declare_its_own(tmp_path):
    (tmp_path / "gates").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "w"\ndependencies = ["openpyxl"]\n', encoding="utf-8")
    (tmp_path / "gates" / "check_lines.py").write_text('# /// script\n# dependencies = ["openpyxl"]\n# ///\nimport openpyxl\n',
                                                         encoding="utf-8")
    (tmp_path / "gates" / "check_report.py").write_text("import openpyxl\n", encoding="utf-8")  # run with python3
    errors, _warnings, _summary = plan_validate.validate(copy.deepcopy(INV), agent_root=tmp_path)
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
    errors, _warnings, _summary = plan_validate.validate(plan, agent_root=tmp_path)
    assert any("'drafter' cannot reach Gmail drafts" in e for e in errors), errors
    agent.write_text("---\nname: drafter\ntools: [read, mcp:gmail/create_draft]\n---\nbody\n", encoding="utf-8")
    errors, warnings, _summary = plan_validate.validate(plan, agent_root=tmp_path)
    assert not any("cannot reach" in e for e in errors) and not any("connector" in w for w in warnings), (errors, warnings)


def test_long_strings_are_written_folded_and_read_back_the_same(tmp_path):
    from utils import load_yaml, write_yaml
    text = " ".join(["Found 8 direct .eml messages covering plain text and HTML-only ones"] * 4)
    path = "/a/very/long/path/" + "x" * 120
    write_yaml(tmp_path / "p.yaml", load_yaml(f'finding: {text}\n\npath: {path}\nquoted: "{text}"\n'))
    written = (tmp_path / "p.yaml").read_text()
    assert "finding: >-\n" in written and max(len(x) for x in written.splitlines() if x.startswith("  ")) < 100
    assert f"{text[-20:]}\n\npath: {path}\n" in written  # the blank line after it kept, not doubled
    assert f"path: {path}\n" in written and f'quoted: "{text}"' in written  # nowhere to break; quoted: kept
    assert load_yaml(written)["finding"] == text


def test_a_changed_revision_alone_needs_no_critic():
    plan = {"status": "ready", "revision": 5, "goal": "g"}
    plan["critique"] = {"plan_digest": plan_validate.plan_digest(plan)}
    assert plan_validate.check_ready({**plan, "revision": 6}) is None
    assert plan_validate.check_ready({**plan, "goal": "other"})  # content: the critic must see it


def test_the_editors_schema_is_the_validators():
    # plan.schema.json is generated from the models; after changing them, write it again:
    # uv run python -c "import json, sys; sys.path.insert(0, '.pi/skills/agent/scripts'); import plan_validate as v; \
    #   open('.pi/skills/agent/plan.schema.json', 'w').write(json.dumps(v.plan_json_schema(), indent=1) + '\n')"
    import json
    schema = plan_validate.plan_json_schema()
    assert json.loads((SKILL / "plan.schema.json").read_text(encoding="utf-8")) == schema
    stage = schema["$defs"]["Stage"]
    assert stage["additionalProperties"] is False and {"after", "reads", "writes", "program", "map"} <= set(stage["properties"])
