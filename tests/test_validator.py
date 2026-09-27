#!/usr/bin/env python3
"""Validator regression: both examples must pass; each broken variant must
report an error (or warning) containing the expected text.

Run: python3 tests/test_validator.py
"""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, ".pi", "skills", "workflow")
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import validate_plan

EX = os.path.join(SKILL, "examples")


def example(name):
    with open(os.path.join(EX, name), encoding="utf-8") as fh:
        return fh.read()


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
    errors, warnings, summary = validate_plan.validate(p, run_state=p.pop("__run__", None))
    return errors, warnings, summary


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


def main():
    failures = 0
    for name, base in (("invoice-check", INV), ("competitor-memo", COMP)):
        errors, _warnings, _summary = validate_plan.validate(copy.deepcopy(base))
        ok = not errors
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} example {name} is valid" + ("" if ok else f": {errors}"))
    for name, base, mutate, expected, level in CASES:
        errors, warnings, _summary = check(base, mutate)
        pool = errors if level == "error" else warnings
        ok = any(expected in m for m in pool)
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f": expected {level} containing '{expected}', got errors={errors} warnings={warnings}"))
    for name, base, mutate, expected in WAVES:
        errors, _warnings, summary = check(base, mutate)
        got = [(w["stages"], w["ends_with"]) for w in summary.get("waves") or []]
        ok = not errors and got == expected
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} waves: {name}" + ("" if ok else f": got {got}, errors={errors}"))
    # Text-level case: an edit that drops a `- id:` line merges two list items;
    # a plain YAML loader would silently keep the second item's values.
    text = example("invoice-check.yaml").replace("  - id: q2\n", "", 1)
    try:
        validate_plan.load_yaml(text)
        ok, got = False, "loaded without error"
    except validate_plan.YAMLError as exc:
        ok, got = "duplicate key" in str(exc), str(exc)
    failures += not ok
    print(f"{'ok  ' if ok else 'FAIL'} repeated key after a dropped list item" + ("" if ok else f": {got}"))
    total = len(CASES) + len(WAVES) + 3
    print(f"\n{total - failures}/{total} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
