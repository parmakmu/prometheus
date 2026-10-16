"""An agent's plan on disk: plan.yaml for people, in sections; how it was planned in .plan/ (owner, 2026-10-05).

The scripts read and write the plan as one (utils.read_plan, write_plan); these check the split, the layout, that
rewriting changes nothing, that an older agent's plan moves on refresh, and that a section left in plan.yaml is
reported.
"""
import sys

from conftest import SKILL, copy_example

sys.path.insert(0, str(SKILL / "scripts"))
from utils import BANNER, PLAN_PARTS, read_plan, read_yaml, write_plan
from validate_plan import plan_digest


def test_the_plan_is_split_and_written_in_its_sections(tmp_path):
    agent = tmp_path / "invoice-check"
    agent.mkdir()
    copy_example("invoice-check", agent)
    plan = read_plan(agent)
    assert set(PLAN_PARTS) <= set(plan)  # read as one
    write_plan(agent, plan)
    text = (agent / "plan.yaml").read_text(encoding="utf-8")
    assert not any(f"\n{p}:" in text for p in PLAN_PARTS)  # each in .plan/<part>.yaml instead
    assert all(read_yaml(agent / ".plan" / f"{p}.yaml", {}).get(p) for p in PLAN_PARTS)
    banners = [line.removeprefix(BANNER).rstrip(" ─") for line in text.splitlines() if line.startswith(BANNER) and line.endswith("─")]
    assert banners == ["What you asked for", "What it works on", "How it works", "Assumptions", "Limits and models"]
    assert "\n\n  - id: " in text  # stages one paragraph each
    # the same plan, and the same text when written again (a banner read back is not repeated)
    assert plan_digest(read_plan(agent)) == plan_digest(plan)
    write_plan(agent, read_plan(agent))
    assert (agent / "plan.yaml").read_text(encoding="utf-8") == text


def test_an_empty_part_has_no_file(tmp_path):
    agent = tmp_path / "a"
    agent.mkdir()
    copy_example("invoice-check", agent)
    plan = read_plan(agent)
    plan["unknowns"] = []
    write_plan(agent, plan)
    assert not (agent / ".plan" / "unknowns.yaml").exists() and "unknowns" not in read_plan(agent)


def test_a_part_left_in_plan_yaml_is_reported(tmp_path, run_py):
    agent = tmp_path / "a"
    (agent / ".pi").mkdir(parents=True)
    copy_example("invoice-check", agent)
    (agent / ".plan" / "questions.yaml").unlink(missing_ok=True)
    with (agent / "plan.yaml").open("a", encoding="utf-8") as f:
        f.write("questions: []\n")
    r = run_py([SKILL / "scripts" / "validate_plan.py", agent / "plan.yaml"])
    assert any("questions: belongs in .plan/questions.yaml" in e for e in r.data["errors"]), r.data["errors"]
