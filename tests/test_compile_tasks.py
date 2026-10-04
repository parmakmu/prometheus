"""Task text the compiler writes for children, checked on a small plan built in
the test (the scenario in test_agent.py covers compiling whole waves)."""
import importlib.util
import sys

from conftest import SKILL

sys.path.insert(0, str(SKILL / "scripts"))  # compile_wave imports its sibling scripts
spec = importlib.util.spec_from_file_location("compile_wave", SKILL / "scripts" / "compile_wave.py")
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)

# emails-to-excel's shape: a reviewed discovery report that a later stage reads
PLAN = {
    "stages": [
        {"key": "discover", "title": "Inspect the emails", "kind": "judgment", "agent": "scout",
         "out": [{"id": "discovery_report", "path": "discovery.md"}],
         "verify": {"review": "reviewer", "check": "Report the available fields."}},
        {"key": "read-emails", "title": "Extract the emails", "kind": "deterministic", "agent": "worker",
         "consumes": ["discovery_report"], "out": [{"id": "email_rows", "path": "rows.json"}],
         "verify": {"check": "Rows keep sender, To/Cc recipients and date."}},
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


def test_a_stage_nobody_consumes_gets_no_such_section(tmp_path):
    c = compiler(tmp_path)
    assert "Used next by" not in c.task("read-emails", c.stages["read-emails"], set())


def test_a_program_runs_with_uv_in_the_agents_environment(tmp_path):
    # the program uses the agent's packages (pyproject.toml); `uv run` syncs them first
    plan = {"stages": [{"key": "export", "title": "Write the workbook", "kind": "deterministic", "pattern": "script",
                        "agent": "worker", "script": "export.py", "run": "rows.json out.xlsx",
                        "out": [{"id": "book", "path": "out.xlsx"}], "verify": {"check": "One row per email."}}]}
    c = cw.Compiler(tmp_path, plan, {}, tmp_path / "runs" / "r1", {}, set())
    assert c.program_command(c.stages["export"]).startswith(f"uv run {tmp_path / 'scripts' / 'export.py'} ")
    task = c.task("export", c.stages["export"], set())
    assert "do not add it yourself" in task and "uv add" not in task
