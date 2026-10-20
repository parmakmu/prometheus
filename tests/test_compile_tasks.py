"""Task text the compiler writes for children, checked on a small plan built in
the test (the scenario in test_agent.py covers compiling whole waves)."""
import importlib.util
import sys

from conftest import SKILL

sys.path.insert(0, str(SKILL / "scripts"))  # compile_wave imports its sibling scripts
spec = importlib.util.spec_from_file_location("compile_wave", SKILL / "scripts" / "run_compile.py")
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)

# emails-to-excel's shape: a reviewed discovery report that a later stage reads
PLAN = {
    "stages": [
        {"id": "discover", "title": "Inspect the emails", "kind": {"value": "judgment"}, "agent": "scout",
         "writes": [{"id": "discovery_report", "path": "discovery.md"}],
         "review": "reviewer", "done_when": "Report the available fields."},
        {"id": "read-emails", "title": "Extract the emails", "kind": {"value": "deterministic"}, "agent": "worker",
         "reads": ["discovery_report"], "writes": [{"id": "email_rows", "path": "rows.json"}],
         "done_when": "Rows keep sender, To/Cc recipients and date."},
    ],
}
USED_BY = "- read-emails (Extract the emails) reads discovery_report; it is done when: Rows keep sender, To/Cc recipients and date."


def compiler(tmp_path):
    return cw.Compiler(tmp_path, PLAN, {}, tmp_path / "runs" / "r1", {}, set())


def test_producer_is_told_what_its_consumers_need(tmp_path):
    c = compiler(tmp_path)
    task = c.task("discover", c.stages["discover"], set())
    assert "Used next by" in task and USED_BY in task


def test_reviewer_judges_by_the_same_consumers(tmp_path):
    # the producer and its reviewer get the same lines, so they apply one standard
    c = compiler(tmp_path)
    assert USED_BY in c.review_step("discover", c.stages["discover"])["review"]["task"]


def test_a_stage_nobody_reads_gets_no_such_section(tmp_path):
    c = compiler(tmp_path)
    assert "Used next by" not in c.task("read-emails", c.stages["read-emails"], set())


def test_a_program_runs_with_uv_in_the_agents_environment(tmp_path):
    # the program uses the agent's packages (pyproject.toml); `uv run` syncs them first
    plan = {"stages": [{"id": "export", "title": "Write the workbook", "kind": {"value": "deterministic"}, "pattern": "script",
                        "agent": "worker", "program": {"file": "export.py", "args": "rows.json out.xlsx"},
                        "writes": [{"id": "book", "path": "out.xlsx"}], "done_when": "One row per email."}]}
    c = cw.Compiler(tmp_path, plan, {}, tmp_path / "runs" / "r1", {}, set())
    assert c.program_command(c.stages["export"]).startswith(f"uv run {tmp_path / 'scripts' / 'export.py'} ")
    task = c.task("export", c.stages["export"], set())
    assert "do not install, fetch or improvise it" in task and ".missing/export.json" in task and "uv add" not in task


def test_check_skips_a_resume_of_an_earlier_waves_stage(tmp_path):
    # before the run, an earlier wave has no launch to resume from: --check must not call that an error
    plan = {"stages": [{"id": "a", "kind": {"value": "judgment"}, "agent": "scout", "writes": [{"id": "x", "path": "x.md"}]},
                       {"id": "b", "kind": {"value": "judgment"}, "resumes": "a", "after": ["a"], "reads": ["x"]}]}
    c = cw.Compiler(tmp_path, plan, {}, tmp_path / "runs" / "r1", {}, set())
    c.item("b", c.stages["b"], set(), "task")
    assert c.errors and set(c.errors) == c.unresolved
