"""End-to-end regression: scaffold an agent, compile and run it (against the
pi-subagents mock), resolve, review and merge, export it. One continuous
scenario: state (the run id, its folder, the
compiled wave...) is built up across the module and later tests depend on
what earlier ones left on disk. Pytest runs a module's tests in the order
they are defined (no randomizing plugin is installed), which this relies on.
"""
import datetime
import json
import os
import shutil
import types
from pathlib import Path

import pytest
from conftest import HERE, ROOT, SKILL, copy_example, fake_critic, git_in, stamp

# one continuous scenario (state on disk, module fixtures): its tests run in order on one worker
pytestmark = pytest.mark.xdist_group("agent-scenario")


def ws(compiled_json_path: Path) -> str:
    """The workflowScript text a compiled wave's call points to (call.workflow)."""
    data = json.loads(Path(compiled_json_path).read_text(encoding="utf-8"))
    return Path(data["call"]["workflow"]).read_text(encoding="utf-8")


def wave(compiled_json_path: Path) -> dict:
    """The wave data a compiled script starts with (`const W = {...};`, before the runner)."""
    return json.loads(ws(compiled_json_path).split("const W = ", 1)[1].split(";\n\n", 1)[0])


def items(compiled_json_path: Path) -> dict:
    """The wave's single-child items by key."""
    return {it["key"]: it for step in wave(compiled_json_path)["steps"] for it in step.get("layer", [])}


@pytest.fixture(scope="module")
def env(tmp_path_factory, run_py):
    """Shared, mutable state for the whole scenario. `env.b` is the scaffolded
    agent's root; `env.run(args)` runs a script with cwd=env.b."""
    tmp = tmp_path_factory.mktemp("agent")
    prometheus = tmp / "prometheus"
    (prometheus / "agents").mkdir(parents=True)
    import shutil
    shutil.copytree(ROOT / ".pi", prometheus / ".pi")
    shutil.copytree(ROOT / "templates", prometheus / "templates")  # beside .pi, as in the repo
    k = prometheus / ".pi" / "skills" / "agent" / "scripts"
    b = prometheus / "agents" / "invoice-check"
    ns = types.SimpleNamespace(
        tmp=tmp, root=ROOT, skill=SKILL, prometheus=prometheus, k=k, b=b,
        run=lambda args, **kw: run_py([k / args[0], *args[1:]], cwd=b, **kw),
        run_in=lambda cwd, args, **kw: run_py([k / args[0], *args[1:]], cwd=cwd, **kw),
        git=None,  # set once b exists (after scaffold)
    )
    return ns


# --- setup: Prometheus copied, scaffold, refresh -----------------------------

def test_prometheus_copied_from_the_repo(env):
    assert (env.prometheus / ".pi").is_dir()


def test_prometheus_has_only_plan_critic(env):
    assert (env.prometheus / ".pi" / "agents" / "plan-critic.md").is_file()
    assert not (env.prometheus / ".pi" / "agents" / "batcher.md").exists()


def test_plan_critic_identical_in_prometheus_and_agent_template(env):
    a = (ROOT / ".pi" / "agents" / "plan-critic.md").read_text(encoding="utf-8")
    b = (ROOT / "templates" / "agent" / ".pi" / "agents" / "plan-critic.md").read_text(encoding="utf-8")
    assert a == b


def test_scaffold_returns_today(env):
    r = env.run_in(env.prometheus, ["agent_scaffold.py", "invoice-check", "--root", "agents", "--request", "invoices"])
    assert r.data and r.data["ok"]
    assert r.data["today"] == datetime.datetime.now().astimezone().date().isoformat()


def test_agent_pyproject_named_after_the_slug(env):
    assert 'name = "agent-invoice-check"' in (env.b / "pyproject.toml").read_text(encoding="utf-8")


def test_agent_has_batcher_and_extension(env):
    assert (env.b / ".pi" / "agents" / "batcher.md").is_file()
    assert (env.b / ".pi" / "extensions" / "agent" / "index.ts").is_file()


def test_agent_is_its_own_git_repo_with_a_first_commit(env):
    env.git = git_in(env.b)
    assert (env.b / ".git").is_dir()
    assert "Create agent invoice-check" in env.git("log", "--format=%s").stdout


def test_agent_declares_no_pi_packages(env):
    assert "\n.pi/npm/" in ("\n" + (env.b / ".gitignore").read_text(encoding="utf-8"))
    assert not (env.b / ".pi" / "npm").exists()
    assert "packages" not in (env.b / ".pi" / "settings.json").read_text(encoding="utf-8")


def test_refresh_keeps_the_lessons_section_of_the_role_file(env):
    role = env.b / ".pi" / "APPEND_SYSTEM.md"
    original = role.read_text(encoding="utf-8")
    role.write_text(original + "\n## Lessons for this agent\n\n- Ask for the folder first.\n", encoding="utf-8")
    r = env.run_in(env.prometheus, ["agent_scaffold.py", "invoice-check", "--root", "agents", "--refresh"])
    assert r.data["ok"]
    refreshed = role.read_text(encoding="utf-8")
    assert refreshed.startswith(original.rstrip("\n")) and refreshed.endswith("- Ask for the folder first.\n")
    assert refreshed.count("## Lessons for this agent") == 1


def test_env_stays_out_of_git(env):
    assert ".env" in (env.b / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert env.git("ls-files", ".env").stdout.strip() == ""
    (env.b / ".env").write_text("", encoding="utf-8")


# --- a concrete plan, gates, the first compile check -------------------------

def test_compile_check(env):
    copy_example("invoice-check", env.b)
    (env.b / "gates" / "check_lines.py").write_text(
        "import json,sys,os\nprint(json.dumps({'ok': all(os.path.exists(p) for p in sys.argv[1:])}))\n", encoding="utf-8")
    (env.b / "gates" / "check_report.py").write_text(
        "import os,sys\nsys.exit(0 if os.path.exists(sys.argv[1]) else 1)\n", encoding="utf-8")
    r = env.run_in(env.prometheus, ["run_compile.py", "--agent", env.b, "--check"])
    assert r.data and r.data["ok"] is True


# --- a run, from compile through both waves -----------------------------------

def test_compile_needs_an_active_run(env):
    plan = env.b / "plan.yaml"
    text = plan.read_text(encoding="utf-8").replace("    batch: 50\n", "    batch: 3\n")
    assert text != plan.read_text(encoding="utf-8")
    plan.write_text(text, encoding="utf-8")
    stamp(plan)  # the critic's result, recorded for the plan as edited
    r = env.run(["run_compile.py"])
    assert "no active run" in (r.data or {}).get("error", "")


def test_new_run_folder(env):
    r = env.run(["run_steps.py", "start-run", "--note", "first"])
    assert r.data and r.data["ok"]
    env.rid = r.data["run"]
    env.run_dir = Path(r.data["path"])  # as the script sees it (/var -> /private/var on macOS)
    env.br = env.run_dir.parent.parent  # the agent root, from the run's own report
    assert (env.run_dir / "run.yaml").is_file()
    assert (env.run_dir / "work").is_dir()


def test_new_run_commits_the_plan(env):
    head = env.git("rev-parse", "HEAD").stdout.strip()
    assert f"commit: {head}" in (env.run_dir / "run.yaml").read_text(encoding="utf-8").splitlines()
    assert f"plan for run {env.rid}" in env.git("log", "-1", "--format=%s").stdout
    assert env.git("status", "--porcelain").stdout.strip() == ""


def test_a_run_refuses_given_values_it_cant_use(env):
    # what an API call gives up front: declared params only, stages that exist
    r = env.run(["run_steps.py", "start-run", "--params", '{"nosuch": "x"}'])
    assert "declares no param 'nosuch'" in (r.data or {}).get("error", "")
    r = env.run(["run_steps.py", "start-run", "--approve", "nosuch"])
    assert "names no stage: nosuch" in (r.data or {}).get("error", "")


def test_preflight_asks_for_params(env):
    r = env.run(["run_compile.py"])
    assert "mail_dir" in (r.data or {}).get("preflight", {}).get("missing_params", [])


def test_relative_and_missing_input_paths_refused(env):
    data_dir = env.tmp / "data" / "mail"
    data_dir.mkdir(parents=True)
    for i in range(1, 8):
        (data_dir / f"m{i}.eml").write_text(f"invoice {i}", encoding="utf-8")
    budget = env.tmp / "data" / "budget.xlsx"
    budget.write_text("x", encoding="utf-8")
    env_file = env.b / ".env"

    env_file.write_text(f"MAIL_DIR=data/mail\nBUDGET_FILE={budget}\n", encoding="utf-8")
    r = env.run(["run_compile.py"])
    assert "relative path" in " ".join((r.data or {}).get("preflight", {}).get("plan_errors", []))

    env_file.write_text(f"MAIL_DIR={env.tmp / 'data' / 'nope'}\nBUDGET_FILE={budget}\n", encoding="utf-8")
    r = env.run(["run_compile.py"])
    assert "does not exist" in " ".join((r.data or {}).get("preflight", {}).get("plan_errors", []))

    env_file.write_text(f"MAIL_DIR={data_dir}\nBUDGET_FILE={budget}\n", encoding="utf-8")


def test_environment_wins_over_dotenv(env):
    r = env.run(["run_compile.py", "--no-commit"], env={**os.environ, "MAIL_DIR": str(env.tmp / "data" / "nope")})
    assert "does not exist" in " ".join((r.data or {}).get("preflight", {}).get("plan_errors", []))
    assert not (env.run_dir / ".compiled").exists()  # a refused compile saves no wave


def test_compile_w1_from_main_tag_r1(env):
    r = env.run(["run_compile.py"])
    assert r.data and r.data["ok"] is True
    assert r.data["run"] == env.rid
    assert r.data["commit"] == env.git("rev-parse", "HEAD").stdout.strip()
    env.w1 = env.run_dir / ".compiled" / "w1.json"
    assert env.w1.is_file()
    assert env.git("symbolic-ref", "--short", "HEAD").stdout.strip() == "main"
    assert env.git("rev-parse", "r1").stdout.strip() == env.git("rev-parse", "HEAD").stdout.strip()


def test_launch_carries_a_script_path_not_the_text(env):
    data = json.loads(env.w1.read_text(encoding="utf-8"))
    assert data["call"].get("workflowScript") is None
    script_path = Path(data["call"]["workflow"])
    assert script_path == env.run_dir / ".compiled" / "w1.js"
    assert script_path.stat().st_size > 0


def test_children_run_in_the_runs_work(env):
    assert f'"cwd": "{env.run_dir / "work"}"' in ws(env.w1) or f'cwd: "{env.run_dir / "work"}"' in ws(env.w1)


def test_producer_task_names_the_stages_that_read_its_output(env):
    # extract's invoice_lines are read by compare (and review) in a later wave
    assert "- compare (" in ws(env.w1) and "Used next by" in ws(env.w1)


def test_gate_points_at_the_agents_gates(env):
    assert f"python3 {env.br / 'gates' / 'check_lines.py'}" in ws(env.w1)


def test_w1_runs_in_the_mock(env, run_node):
    r = run_node([HERE / "mock-subagents.mjs", env.w1])
    env.w1_result = r.data
    assert r.data and r.data["result"]["status"] == "done"
    (env.tmp / "w1.json").write_text(json.dumps(r.data), encoding="utf-8")


def test_children_outcomes_are_named(env):
    # pi-subagents' results carry ok, not a status; the script names the outcome
    assert {c["status"] for c in env.w1_result["result"]["children"].values()} == {"completed"}


def test_map_stage_launched_three_batch_children(env):
    keys = [e["key"] for e in env.w1_result["log"] if e["key"].startswith("find-") and e["key"][5:].isdigit()]
    assert len(keys) == 3
    # and its merge stored each email's record, for a later run to leave that email out (TASKS 38)
    listed = json.loads((env.run_dir / "work" / "find" / "items.json").read_text(encoding="utf-8"))
    stored = list((env.run_dir / "work" / "find" / "items").glob("*.json"))
    assert listed["named"] and len(stored) == len(listed["items"]) > 0


def test_report_written_to_the_runs_outputs(env):
    assert (env.run_dir / "outputs" / "Q3-invoice-variance.xlsx").is_file()
    assert not (env.b / "outputs").exists()


def test_failed_child_stops_the_wave(env, run_node):
    r = run_node([HERE / "mock-subagents.mjs", env.w1, "--fail", "extract"])
    assert r.data and r.data["result"]["status"] == "failed"
    # runs.all returns the failed child ({ok: false}); the script stops at it
    assert r.data["result"]["at"] == ["extract"]
    assert r.data["result"]["children"]["extract"]["status"] == "failed"


def test_failed_runs_run_child_fails_the_wave(env, run_node):
    # runs.run throws on a failed child (the map stage's split here)
    r = run_node([HERE / "mock-subagents.mjs", env.w1, "--fail", "find-split"])
    assert r.data and r.data["result"]["status"] == "failed"
    assert "find-split" in r.data["result"]["error"]


def test_blocked_only_applies_to_review_children(env, run_node):
    r = run_node([HERE / "mock-subagents.mjs", env.w1, "--block", "review"])
    assert r.data and r.data["result"]["status"] == "done"


def test_start_wave_refuses_a_wave_that_is_not_next(env):
    r = env.run(["run_steps.py", "start-wave", "--wave", "w2", "--subagent-run-id", "wf-0"])
    assert "not the next wave" in (r.data or {}).get("error", "")


def test_start_wave(env):
    plan_before = (env.b / "plan.yaml").read_text(encoding="utf-8")
    r = env.run(["run_steps.py", "start-wave", "--wave", "w1", "--subagent-run-id", "wf-1", "--mission-id", "m-1"])
    assert r.data and r.data["ok"]
    assert (env.b / "plan.yaml").read_text(encoding="utf-8") == plan_before
    run_yaml = (env.run_dir / "run.yaml").read_text(encoding="utf-8")
    assert run_yaml.count(": running") == 6


def test_running_wave_cannot_relaunch(env):
    r = env.run(["run_compile.py"])
    assert "running" in (r.data or {}).get("error", "")


def test_launch_records_the_plans_commit(env):
    head = env.git("rev-parse", "HEAD").stdout.strip()
    assert f"commit: {head}" in (env.run_dir / "run.yaml").read_text(encoding="utf-8")


def test_script_task_new(env):
    assert f"Write this stage's program at {env.br / 'scripts' / 'extract_invoice_lines.py'}" in ws(env.w1)


def test_nothing_commits_while_a_wave_runs(env, python_exe):
    with (env.b / "gates" / "check_report.py").open("a", encoding="utf-8") as fh:
        fh.write("# edited during the wave\n")
    (env.b / "scripts").mkdir(exist_ok=True)
    (env.b / "scripts" / "extract_invoice_lines.py").write_text('"""usage: extract_invoice_lines.py MAIL OUT"""\n', encoding="utf-8")
    tree = env.git("write-tree").stdout.strip()
    import subprocess
    r = subprocess.run([python_exe, "-c", f"import agent_repo; print(agent_repo.commit({str(env.b)!r}, 'x')[1])"],
                        cwd=env.k, capture_output=True, text=True, check=False)
    assert "a wave is running" in r.stdout
    assert env.git("write-tree").stdout.strip() == tree


def test_refresh_refused_during_an_active_run(env):
    skill_file = env.b / ".pi" / "skills" / "agent" / "SKILL.md"
    before = skill_file.stat().st_mtime_ns
    r = env.run_in(env.prometheus, ["agent_scaffold.py", "invoice-check", "--root", "agents", "--refresh"])
    assert r.data and r.data["ok"] is False
    assert "active run" in r.data["error"]
    assert skill_file.stat().st_mtime_ns == before  # nothing rewritten


def test_finish_refuses_while_pi_subagents_still_runs_the_wave(env):
    (env.tmp / "w1-ret.json").write_text(json.dumps(env.w1_result["result"]), encoding="utf-8")
    env.main0 = env.git("rev-parse", "main").stdout.strip()
    pis = env.tmp / "pi-subagents" / "async-subagent-runs" / "wf-1"
    pis.mkdir(parents=True)
    (pis / "status.json").write_text('{"state": "running"}', encoding="utf-8")
    env.pis_root = env.tmp / "pi-subagents"
    r = env.run(["run_steps.py", "finish-wave", "--wave", "w1"], env={**os.environ, "PI_SUBAGENTS_TEMP_ROOT": str(env.pis_root)})
    assert "still running" in (r.data or {}).get("error", "")


def test_finish_reads_outcome_and_cost_from_pi_subagents(env):
    returned = json.loads((env.tmp / "w1-ret.json").read_text(encoding="utf-8"))
    status = {"state": "complete", "workflow": {"value": returned},
              "totalCost": {"costUsd": 0.0123, "inputTokens": 10, "outputTokens": 900}}
    (env.pis_root / "async-subagent-runs" / "wf-1" / "status.json").write_text(json.dumps(status), encoding="utf-8")
    r = env.run(["run_steps.py", "finish-wave", "--wave", "w1"], env={**os.environ, "PI_SUBAGENTS_TEMP_ROOT": str(env.pis_root)})
    assert r.data, r
    env.fin = r.data
    assert (r.data["wave_status"], r.data["run_status"], r.data["ends_with"]) == ("done", "active", "none")
    assert json.loads((env.run_dir / ".compiled" / "w1.json").read_text(encoding="utf-8"))["result"]["status"] == "done"
    assert sorted(p.name for p in (env.run_dir / ".compiled").iterdir()) == ["w1.js", "w1.json"]
    # /agent-history reads each stage's outcome from that record
    shown = env.run(["run_history.py", "show"]).data
    stages = next(r for r in shown["runs"] if r["id"] == env.rid)["stages"]
    children = returned["children"]
    assert stages and all(st[-1]["status"] == children[key]["status"] for key, st in stages.items() if key in children), stages
    assert any(key in children for key in stages)
    run_yaml = (env.run_dir / "run.yaml").read_text(encoding="utf-8")
    assert "ends_with: none" in run_yaml
    assert "cost_usd: 0.0123" in run_yaml


def test_finish_returns_a_children_summary_not_their_outputs(env):
    assert "structuredOutput" not in json.dumps(env.fin["children"])
    assert set(env.fin["children"]["extract"]) == {"status", "output"}


def test_finish_names_the_next_wave_so_no_check_is_needed(env):
    nxt = env.fin["next_wave"]
    assert nxt and nxt["id"] != "w1" and nxt["stages"] and "ends_with" in nxt
    assert "entry" not in env.fin
    # what compile would ask first, so the parent asks the user before compiling, not after
    assert [x["stage"] for x in nxt["preflight"]["decisions_needed"]] == ["approve-drafts"]
    assert "drafts" in [x["stage"] for x in nxt["preflight"]["approvals_needed"]]
    assert "missing_params" not in nxt["preflight"]  # empty lists are left out


def test_finish_marks_the_waves_stages_done(env):
    run_yaml = (env.run_dir / "run.yaml").read_text(encoding="utf-8")
    assert run_yaml.count(": done") == 6
    assert ": running" not in run_yaml


def test_finish_commits_files_the_wave_wrote_on_the_run_branch(env):
    assert env.git("symbolic-ref", "--short", "HEAD").stdout.strip() == f"run/{env.rid}"
    assert env.git("rev-parse", "main").stdout.strip() == env.main0
    assert "scripts/extract_invoice_lines.py" in env.git("show", "--stat", "--format=%s", "HEAD").stdout
    assert "wave w1: files the wave wrote" in env.git("log", "-1", "--format=%s").stdout


def test_next_wave_is_w2_and_asks_for_the_decision(env):
    r = env.run(["run_compile.py"])
    assert r.data and r.data["wave"] == "w2"
    assert [x["stage"] for x in r.data["preflight"]["decisions_needed"]] == ["approve-drafts"]


def test_decision_refused_for_a_non_decision_stage(env):
    r = env.run(["run_steps.py", "decision", "--stage", "drafts", "--answer", '"yes"'])
    assert r.data and r.data["ok"] is False


def test_decision_recorded_in_the_runs_work(env):
    r = env.run(["run_steps.py", "decision", "--stage", "approve-drafts", "--answer", '{"approved": true}'])
    assert r.data and r.data["path"].startswith(str(env.run_dir / "work"))


def test_side_effect_needs_approval(env):
    r = env.run(["run_compile.py"])
    assert r.data, r
    needed = [(x["stage"], bool(x["done_when"]), len(x["reads"])) for x in r.data["preflight"]["approvals_needed"]]
    assert ("drafts", True, 2) in needed


def test_approved_w2_compiles_and_reuses_the_runs_mission(env):
    r = env.run(["run_compile.py", "--approve", "drafts"])
    assert r.data and r.data["call"].get("missionId") == "m-1"


def test_gated_stages_compile_to_explicit_acceptance(env):
    acceptance = items(env.w1)["extract"]["acceptance"]
    assert {k: acceptance[k] for k in ("level", "preserveStagedIndex", "review")} == \
        {"level": "verified", "preserveStagedIndex": True, "review": {"required": False}}
    assert acceptance["verify"][0]["id"] == "gate"


_PI_SUBAGENTS = Path.home() / ".pi" / "agent" / "npm" / "node_modules" / "pi-subagents" / "src" / "workflows" / "scripted-workflow.js"


@pytest.mark.skipif(not _PI_SUBAGENTS.is_file(), reason="needs pi-subagents installed globally (pi install npm:pi-subagents)")
def test_pi_subagents_accepts_the_compiled_waves_and_the_critic_script(env, run_node):
    # pi-subagents' own pre-launch check (syntax, no nested async functions, keys)
    check = env.tmp / "check-script.mjs"
    check.write_text(f"""import fs from "node:fs";
import {{ validateWorkflowScript }} from {json.dumps(str(_PI_SUBAGENTS))};
console.log(JSON.stringify(process.argv.slice(2).map((f) => validateWorkflowScript(fs.readFileSync(f, "utf8")))));
""", encoding="utf-8")
    scripts = [*sorted((env.run_dir / ".compiled").glob("*.js")), SKILL / "references" / "critic.js"]  # + the critic launch
    r = run_node([check, *scripts])
    assert scripts and r.data == [{"ok": True, "errors": []}] * len(scripts), r


def test_every_wave_runs_the_same_runner(env):
    # the compiler writes only data; the code is wave_runner.js, appended unchanged
    runner = (SKILL / "scripts" / "wave_runner.js").read_text(encoding="utf-8")
    assert ws(env.w1).endswith(runner) and wave(env.w1)["wave"] == "w1"


# --- resolving -----------------------------------------------------------------

def test_resolve_refuses_a_stage_done_in_the_active_run(env):
    r = env.run(["plan_edit.py", "resolve", "--change", '{"stages":[{"id":"find","kind":{"value":"judgment"}}]}'])
    assert "history" in (r.data or {}).get("error", "")


def test_resolve_refuses_waves(env):
    r = env.run(["plan_edit.py", "resolve", "--change", '{"waves":[]}'])
    assert "not part of the plan" in (r.data or {}).get("error", "")


def test_resolve_refuses_to_weaken_a_side_effect(env):
    r = env.run(["plan_edit.py", "resolve", "--change", '{"stages":[{"id":"drafts","kind":{"value":"deterministic"}}]}'])
    assert "its kind must stay side_effect" in (r.data or {}).get("error", "")


def test_resolve_refuses_to_change_the_goal(env):
    r = env.run(["plan_edit.py", "resolve", "--change", '{"goal":"something else"}'])
    assert "locked" in (r.data or {}).get("error", "")


def test_resolve_of_a_pending_stage_commits(env):
    from ruamel.yaml import YAML
    plan = YAML(typ="safe").load((env.b / "plan.yaml").read_text(encoding="utf-8"))
    st = next(s for s in plan["stages"] if s["id"] == "approve-drafts")
    st = json.loads(json.dumps(st, default=str))
    st["title"] = "Confirm the drafts (resolved)"
    change = {"stages": [st]}
    (env.tmp / "change.json").write_text(json.dumps(change), encoding="utf-8")
    r = env.run(["plan_edit.py", "resolve", "--change", str(env.tmp / "change.json")])
    assert r.data, r
    assert (r.data["ok"], r.data["revision"], r.data["status"]) == (True, 2, "draft")
    assert not (env.b / ".pi" / "history").exists()
    assert env.git("log", "-1", "--format=%s").stdout.strip() == "r2 draft: resolve"
    assert r.data["commit"] == env.git("rev-parse", "HEAD").stdout.strip()
    assert r.data["previous_commit"] == env.git("rev-parse", "HEAD~1").stdout.strip()
    assert "revision: 1" in env.git("show", "HEAD~1:plan.yaml").stdout.splitlines()


def test_record_critique_marks_the_plan_ready(env):
    fake_critic("c-2")
    r = env.run(["plan_edit.py", "critique", "--result", '{"run_id":"c-2","verdict":"pass","findings":[]}'])
    assert r.data and r.data["ready"] is True
    assert env.git("log", "-1", "--format=%s").stdout.strip() == "r2 ready: critic pass"
    assert env.git("rev-parse", "-q", "--verify", "r2").returncode != 0


def test_params_relative_path_reported(env):
    r = env.run(["plan_edit.py", "params", "--values", '{"mail_dir":"data/mail"}'])
    assert "relative path" in " ".join((r.data or {}).get("errors", []))  # the validation report, not a refusal


def test_params_absolute_path_accepted(env):
    mail_dir = env.tmp / "data" / "mail"
    r = env.run(["plan_edit.py", "params", "--values", json.dumps({"mail_dir": str(mail_dir)})])
    assert r.data and r.data["errors"] == []
    assert f"MAIL_DIR='{mail_dir}'" in (env.b / ".env").read_text(encoding="utf-8").splitlines()


def test_launch_records_the_param_values_it_ran_with(env):
    mail_dir = env.tmp / "data" / "mail"
    assert f"mail_dir: {mail_dir}" in (env.run_dir / "run.yaml").read_text(encoding="utf-8")


# --- review, merge, a second run -------------------------------------------------

def test_stop_keeps_the_run_and_returns_its_branch_for_review(env):
    r = env.run(["run_steps.py", "stop", "--note", "test"])
    assert r.data, r
    assert (r.data["run_status"], r.data["review"]["branch"], len(r.data["review"]["commits"])) == ("stopped", f"run/{env.rid}", 3)
    assert "status: stopped" in (env.run_dir / "run.yaml").read_text(encoding="utf-8").splitlines()


def test_a_new_run_starts_from_main_and_keeps_the_branch_waiting_for_review(env):
    r = env.run(["run_steps.py", "start-run"])
    assert r.data, r
    assert r.data["left_branch"] == f"run/{env.rid}" and env.git("branch", "--show-current").stdout.strip() == "main"
    assert env.git("merge-base", "--is-ancestor", f"{env.main0}", f"run/{env.rid}").returncode == 0  # the kept branch still has its commits
    # put the scenario back as it was: the extra run and its branch gone, the kept branch checked out again
    shutil.rmtree(Path(r.data["path"]))
    env.git("switch", "-q", f"run/{env.rid}")
    assert env.git("branch", "--list", f"run/{r.data['run']}").stdout.strip() == ""  # the new run made no branch yet


def test_merge_run_branch(env):
    steps = int(env.git("rev-list", "--count", f"main..run/{env.rid}").stdout)
    r = env.run(["plan_edit.py", "merge", "--action", "merge"])
    assert r.data, r
    # one commit on main for the whole run, however many the run's branch made
    assert steps > 1 and env.git("rev-list", "--count", f"{env.main0}..main").stdout.strip() == "1"
    assert env.git("log", "-1", "--format=%s", "main").stdout.strip() == f"r2: run/{env.rid}"
    assert (r.data["ok"], r.data["revision"], r.data["unmerged"]) == (True, 2, [])
    assert env.git("symbolic-ref", "--short", "HEAD").stdout.strip() == "main"
    assert env.git("merge-base", "--is-ancestor", env.main0, "main").returncode == 0
    assert env.git("rev-parse", "r2").stdout.strip() == env.git("rev-parse", "main").stdout.strip()
    assert env.git("rev-parse", "-q", "--verify", f"run/{env.rid}").returncode != 0


def test_second_run_starts_from_merged_main(env):
    r = env.run(["run_steps.py", "start-run"])
    assert r.data
    env.rid2 = r.data["run"]
    assert env.rid2 != env.rid
    r = env.run(["run_compile.py"])
    assert r.data and (r.data["ok"], r.data["run"], r.data["wave"]) == (True, env.rid2, "w1")


def test_second_run_reuses_the_merged_program(env):
    assert f"This stage's program is {env.br / 'scripts' / 'extract_invoice_lines.py'}" in ws(env.b / "runs" / env.rid2 / ".compiled" / "w1.json")


def test_an_existing_program_runs_as_a_command(env):
    # no model: batcher runs `uv run <program> <args>` and then the gate
    extract = items(env.b / "runs" / env.rid2 / ".compiled" / "w1.json")["extract"]
    request = json.loads(extract["task"].split("BATCH_TOOL_REQUEST ", 1)[1])
    assert extract["agent"] == "batcher"
    assert request["command"].startswith(f"uv run {env.br / 'scripts' / 'extract_invoice_lines.py'} ")
    assert request["command"].endswith("invoice-index.json invoice-lines.csv unparsed.json")
    assert "check_lines.py" in request["gate"] and request["gate_json"] is True
    assert extract["fix"]["key"] == "extract-fix" and extract["fix"]["agent"] == "worker"


def test_the_command_runs_the_program_without_a_model(env, run_node):
    # a program that does its job (writes its outputs from its arguments) instead of the
    # merged stub, put back afterwards so the agent's repo stays clean
    program = env.b / "scripts" / "extract_invoice_lines.py"
    merged = program.read_text(encoding="utf-8")
    program.write_text("import pathlib, sys\n_, mail, index, lines, unparsed = sys.argv\n"
                       "pathlib.Path(lines).write_text('invoice,po\\n')\npathlib.Path(unparsed).write_text('[]')\n",
                       encoding="utf-8")
    try:
        r = run_node([HERE / "mock-subagents.mjs", env.b / "runs" / env.rid2 / ".compiled" / "w1.json"])
    finally:
        program.write_text(merged, encoding="utf-8")
    assert r.data and r.data["result"]["status"] == "done", r
    ran = {e["key"]: e["agent"] for e in r.data["log"]}
    assert ran["extract"] == "batcher" and "extract-fix" not in ran
    assert (env.b / "runs" / env.rid2 / "work" / "invoice-lines.csv").read_text(encoding="utf-8") == "invoice,po\n"


def test_a_failing_program_gets_a_fix_child(env, run_node):
    r = run_node([HERE / "mock-subagents.mjs", env.b / "runs" / env.rid2 / ".compiled" / "w1.json", "--fail", "extract"])
    assert r.data and r.data["result"]["status"] == "done", r
    fix = next(e for e in r.data["log"] if e["key"] == "extract-fix")
    assert fix["agent"] == "worker" and "mock failure" in fix["task"] and fix["acceptance"]["verify"]
    assert r.data["result"]["children"]["extract"]["status"] == "failed"


def test_check_reports_the_last_run(env):
    r = env.run(["plan_validate.py", "plan.yaml"])
    assert r.data, r
    last = r.data["summary"]["last_run"]
    assert (last["id"], last["runs"]) == (env.rid2, 2)


def test_failure_without_a_return_value_recorded_with_failure_kind(env):
    env.run(["run_steps.py", "start-wave", "--wave", "w1", "--subagent-run-id", "wf-9"])
    run_dir = env.tmp / "pi-subagents" / "async-subagent-runs" / "wf-9"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(json.dumps({
        "state": "failed", "error": "workflow timed out after 600000 ms", "workflow": {"failureKind": "timeout"},
    }), encoding="utf-8")
    r = env.run(["run_steps.py", "finish-wave", "--wave", "w1"], env={**os.environ, "PI_SUBAGENTS_TEMP_ROOT": str(env.tmp / "pi-subagents")})
    assert r.data, r
    assert r.data["wave_status"] == "pending"
    assert r.data["failure_kind"] == "timeout"
    assert "timed out" in r.data["error"]


def test_a_run_that_changed_nothing_has_no_branch_to_review(env):
    r = env.run(["run_steps.py", "stop"])
    assert r.data and r.data["review"] is None


def test_agent_revise_change_branch_from_main(env):
    r = env.run(["plan_edit.py", "begin-change"])
    assert r.data, r
    today = datetime.datetime.now().astimezone().date().isoformat()
    assert (r.data["ok"], r.data["branch"], r.data["revision"]) == (True, f"revise/{today}-1", 2)
    assert "status: ready" in (env.b / "plan.yaml").read_text(encoding="utf-8").splitlines()  # untouched until edited


def test_direct_edits_on_the_change_branch_critique_marks_it_ready(env):
    plan_path = env.b / "plan.yaml"
    text = plan_path.read_text(encoding="utf-8")
    plan_path.write_text(text.replace("goal: >-\n", "goal: >-\n  Changed on a branch.\n", 1), encoding="utf-8")
    fake_critic("c-3")
    r = env.run(["plan_edit.py", "critique", "--result", '{"run_id":"c-3","verdict":"pass","findings":[]}'])
    assert r.data and r.data["ready"] is True and r.data["revision"] == 3  # an edited plan is a new revision


def test_merged_change_goal_changed_on_main_tag_r3(env):
    before = env.git("rev-parse", "main").stdout.strip()
    revise = env.git("symbolic-ref", "--short", "HEAD").stdout.strip()
    r = env.run(["plan_edit.py", "merge", "--action", "merge"])
    assert r.data, r
    assert revise.startswith("revise/") and env.git("rev-list", "--count", f"{before}..main").stdout.strip() == "1"
    assert env.git("log", "-1", "--format=%s", "main").stdout.strip() == f"r3: {revise}"
    assert (r.data["ok"], r.data["revision"]) == (True, 3)
    assert "Changed on a branch" in env.git("show", "main:plan.yaml").stdout
    assert env.git("rev-parse", "r3").stdout.strip() == env.git("rev-parse", "main").stdout.strip()


def test_a_draft_change_cannot_merge_discard_leaves_main_unchanged(env):
    r = env.run(["plan_edit.py", "begin-change"])
    assert r.data and r.data["ok"]
    plan_path = env.b / "plan.yaml"  # an edit the critic has not seen
    plan_path.write_text(plan_path.read_text(encoding="utf-8").replace("goal: >-\n", "goal: >-\n  Not reviewed.\n", 1), encoding="utf-8")
    r = env.run(["plan_edit.py", "merge", "--action", "merge"])
    assert "only a ready, valid plan" in (r.data or {}).get("error", "")
    r = env.run(["plan_edit.py", "merge", "--action", "discard"])
    assert r.data and (r.data["ok"], r.data["revision"]) == (True, 3)
    assert env.git("branch", "--list", "revise/*").stdout.strip() == ""


def test_third_run_starts(env):
    r = env.run(["run_steps.py", "start-run"])
    assert r.data
    env.rid3 = r.data["run"]


# --- export ------------------------------------------------------------------------

def test_fresh_export(env):
    dest = env.tmp / "fresh"
    r = env.run_in(env.prometheus, ["agent_export.py", "agents/invoice-check", str(dest)])
    assert r.data, r
    assert (r.data["ok"], r.data["valid"]) == (True, True)
    assert list((dest / "invoice-check" / "runs").iterdir()) == []
    import subprocess
    count = subprocess.run(["git", "-C", str(dest / "invoice-check"), "rev-list", "--count", "HEAD"],
                            capture_output=True, text=True, check=False).stdout.strip()
    assert count == "1"
    env.fresh_export = dest / "invoice-check"


def test_export_dotenv_lists_variables_without_values(env):
    lines = (env.fresh_export / ".env").read_text(encoding="utf-8").splitlines()
    assert "MAIL_DIR=" in lines
    assert not any(str(env.tmp / "data") in line for line in lines)


def test_keep_state_export_keeps_runs_and_git_history(env):
    (env.b / ".pi" / "npm" / "node_modules").mkdir(parents=True)
    (env.b / ".venv" / "bin").mkdir(parents=True, exist_ok=True)  # `uv run` may have made it already
    dest = env.tmp / "elsewhere"
    r = env.run_in(env.prometheus, ["agent_export.py", "agents/invoice-check", str(dest), "--keep-state"])
    assert r.data and r.data["ok"]
    assert (dest / "invoice-check" / "runs" / env.rid / "run.yaml").is_file()
    import subprocess
    dest_head = subprocess.run(["git", "-C", str(dest / "invoice-check"), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=False).stdout.strip()
    assert dest_head == env.git("rev-parse", "HEAD").stdout.strip()
    env.keep_state_export = dest / "invoice-check"


def test_export_leaves_out_npm_venv_and_dotenv_values(env):
    assert not (env.keep_state_export / ".pi" / "npm").exists()
    assert not (env.keep_state_export / ".venv").exists()
    assert str(env.tmp / "data") not in (env.keep_state_export / ".env").read_text(encoding="utf-8")


# --- extensions (needs npm install here, and pi installed globally with npm) -----

import shutil as _shutil
import subprocess as _subprocess

_npm_root = _subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, check=False).stdout.strip()
_pi_pkg = Path(_npm_root) / "@earendil-works" / "pi-coding-agent" if _npm_root else None
_has_extensions_deps = bool(
    _pi_pkg and _pi_pkg.is_dir() and (ROOT / "node_modules" / "typescript").is_dir()
)
skip_extensions = pytest.mark.skipif(
    not _has_extensions_deps, reason="needs `npm install` in the repo root, and pi installed globally with npm"
)


@pytest.fixture(scope="module")
def ext(env, tmp_path_factory):
    """The Node harness environment for driving the two extensions: symlinked
    node_modules (Pi's own package + typebox + @types/node), the .ts files
    copied where tsconfig.json expects them, and the harness script."""
    if not _has_extensions_deps:
        pytest.skip("needs `npm install` in the repo root, and pi installed globally with npm")
    tmp = tmp_path_factory.mktemp("ext")
    nm = tmp / "node_modules"
    (nm / "@earendil-works").mkdir(parents=True)
    (nm / "@types").mkdir(parents=True)
    (tmp / ".ext").mkdir()
    (nm / "@earendil-works" / "pi-coding-agent").symlink_to(_pi_pkg, target_is_directory=True)
    (nm / "typebox").symlink_to(_pi_pkg / "node_modules" / "typebox", target_is_directory=True)
    (nm / "@types" / "node").symlink_to(ROOT / "node_modules" / "@types" / "node", target_is_directory=True)
    _shutil.copy(ROOT / ".pi" / "extensions" / "prometheus" / "index.ts", tmp / ".ext" / "prometheus.ts")
    _shutil.copy(ROOT / "templates" / "agent" / ".pi" / "extensions" / "agent" / "index.ts", tmp / ".ext" / "agent.ts")
    _shutil.copy(ROOT / "prometheus" / "release_runner.ts", tmp / ".ext" / "release_runner.ts")  # type-checked with the others
    _shutil.copy(HERE / "tsconfig.json", tmp / "tsconfig.json")
    (tmp / "package.json").write_text('{"type": "module"}\n')  # ES modules, as pi loads extensions (import.meta)
    _shutil.copy(HERE / "ext-harness.mjs", tmp / "h.mjs")
    pi_version = json.loads((_pi_pkg / "package.json").read_text(encoding="utf-8"))["version"]
    return types.SimpleNamespace(tmp=tmp, pi_version=pi_version)


def drive(ext, entry, cwd, steps, extra_env=None):
    """Run the harness against `entry` (an extension's index.ts) with `cwd`, driving
    it through `steps`; returns the parsed {commands, tools, steps: [...]} report."""
    steps_file = ext.tmp / f"steps-{len(list(ext.tmp.glob('steps-*.json')))}.json"
    steps_file.write_text(json.dumps(steps), encoding="utf-8")
    env_vars = {**os.environ, **(extra_env or {})}
    r = _subprocess.run(
        ["node", "--experimental-strip-types", "--no-warnings", "h.mjs", str(entry), str(cwd), str(steps_file)],
        cwd=ext.tmp, env=env_vars, capture_output=True, text=True, check=False,
    )
    try:
        return json.loads(r.stdout)
    except ValueError:
        pytest.fail(f"harness produced no JSON: {r.stdout!r} {r.stderr!r}")


@skip_extensions
def test_typecheck_against_pi_types(ext):
    tsc = ROOT / "node_modules" / ".bin" / "tsc"
    r = _subprocess.run([str(tsc), "-p", str(ext.tmp / "tsconfig.json")], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout + r.stderr


@skip_extensions
def test_prometheus_extension_registers_commands_and_tools(env, ext):
    report = drive(ext, ext.tmp / ".ext" / "prometheus.ts", env.prometheus, [
        {"cmd": "agent-list", "args": ""},
        {"tool": "agent_check", "params": {"slug": "invoice-check"}},
        {"cmd": "agent-list", "complete": "inv"},
    ])
    env.prometheus_ext_report = report
    assert (len(report["commands"]), len(report["tools"])) == (4, 4)  # agent_scaffold, agent_check, agent_record_critique, agent_add_package


@skip_extensions
def test_agent_check_tool(env):
    assert env.prometheus_ext_report["steps"][1]["result"]["valid"] is True


@skip_extensions
def test_slug_completion(env):
    assert env.prometheus_ext_report["steps"][2]["completions"][0]["value"] == "invoice-check"


@skip_extensions
def test_scaffold_needs_agent_new(env, ext):
    # invoice-check exists, so a scaffold the gate lets through is refused by the script and creates nothing
    scaffold = {"tool": "agent_scaffold", "params": {"slug": "invoice-check", "request": "x"}}
    report = drive(ext, ext.tmp / ".ext" / "prometheus.ts", env.prometheus, [
        scaffold,
        {"toolCall": {"toolName": "bash", "input": {"command": "python .pi/skills/agent/scripts/agent_scaffold.py x"}}},
        {"cmd": "agent-new", "args": "read my emails"},
        scaffold,
    ])
    refused, bash, new, allowed = report["steps"]
    assert "only through /agent-new" in refused["error"]
    assert bash["blocked"] is True
    assert new["out"][0]["kind"] == "prompt"
    assert "already has a plan" in allowed["result"]["error"]
    # /agent-new activates subagent (for probes and the critic) before its prompt goes out
    assert "subagent" not in refused["activeTools"]
    assert {"subagent", "bg_wait", "subagent_supervisor", "contact_supervisor"} <= set(new["activeTools"])  # off until delegating


@skip_extensions
def test_prometheus_records_a_new_agents_critic_which_makes_it_ready(env, ext, run_py):
    # first planning: a critique written by hand (no fingerprint) is not ready; agent_record_critique checks the critic
    # ran and stamps the plan, as it does inside an agent
    from ruamel.yaml import YAML
    agents = env.prometheus / "agents"
    assert run_py([SKILL / "scripts" / "agent_scaffold.py", "fresh", "--root", agents, "--request", "x"]).data["ok"]
    fresh = agents / "fresh"
    copy_example("invoice-check", fresh)
    for g in (env.b / "gates").glob("*.py"):
        shutil.copy(g, fresh / "gates" / g.name)
    critique = fresh / ".plan" / "critique.yaml"
    plan = {"critique": YAML(typ="safe").load(critique.read_text(encoding="utf-8"))["critique"]}
    by_hand = critique.read_text(encoding="utf-8").replace(f"  plan_digest: {plan['critique']['plan_digest']}", "  # none")
    critique.write_text(by_hand, encoding="utf-8")
    fake_critic("c-prom")
    report = drive(ext, ext.tmp / ".ext" / "prometheus.ts", env.prometheus, [
        {"toolCall": {"toolName": "write", "input": {"path": "agents/fresh/.plan/critique.yaml"}}},
        {"tool": "agent_check", "params": {"slug": "fresh"}},
        {"tool": "agent_record_critique", "params": {"slug": "fresh", "run_id": "c-prom", "verdict": "pass", "findings": plan["critique"]["findings"]}},
    ])
    written, hand, recorded = report["steps"]
    assert written["blocked"] and "agent_record_critique" in written["reason"]
    assert any("not recorded with agent_record_critique" in e for e in hand["result"]["errors"]), hand
    assert recorded["schemaOk"] and recorded["result"]["ready"] is True, recorded
    stamped = YAML(typ="safe").load(critique.read_text(encoding="utf-8"))
    assert stamped["critique"]["run_id"] == "c-prom" and stamped["critique"]["plan_digest"]
    shutil.rmtree(fresh)


@skip_extensions
def test_missing_packages_message(env, ext):
    bare = ext.tmp / "bare"
    _subprocess.run(["uv", "venv", "-q", str(bare)], check=False)
    report = drive(ext, ext.tmp / ".ext" / "prometheus.ts", env.prometheus,
                    [{"tool": "agent_check", "params": {"slug": "invoice-check"}}],
                    extra_env={"PI_AGENT_PYTHON": str(bare / "bin" / "python")})
    assert "Do not install packages yourself" in report["steps"][0]["result"]["errors"][0]


@skip_extensions
def test_packages_are_added_only_through_the_approval_tool(env, ext):
    # a new Python package is the user's call: no uv add / pip install from the model, and the tool refuses without a user to ask
    # also every other way to a package: uvx, uv run --with, writing the package list itself
    commands = ("uv add openpyxl", "pip install x", "python -m pip3 install x", "python .pi/skills/agent/scripts/agent_packages.py x --reason y",
                "uvx ruff check", "uv run --with openpyxl scripts/a.py", "sed -i s/a/b/ pyproject.toml", "uv run scripts/a.py", "cat pyproject.toml")
    blocked = [True] * 7 + [False, False]
    for name, cwd, tool_args, project in (("prometheus", env.prometheus, {"slug": "invoice-check"}, "agents/invoice-check/pyproject.toml"),
                                          ("agent", env.b, {}, "pyproject.toml")):
        steps = [{"toolCall": {"toolName": "bash", "input": {"command": c}}} for c in commands]
        steps += [{"toolCall": {"toolName": "edit", "input": {"path": project}}}, {"toolCall": {"toolName": "write", "input": {"path": project.replace("pyproject.toml", "uv.lock")}}}]
        steps.append({"tool": "agent_add_package", "params": {"packages": ["openpyxl"], "reason": "", **tool_args}})
        report = drive(ext, ext.tmp / ".ext" / f"{name}.ts", cwd, steps)
        assert [s["blocked"] for s in report["steps"][:-1]] == [*blocked, True, True], name
        assert "agent_add_package" in report["steps"][0]["reason"], name
        assert "error" in report["steps"][-1], name  # no reason (or no one to ask): nothing is installed


@skip_extensions
def test_agent_extension_registers_commands_and_tools(env, ext):
    steps = [
        {"cmd": "agent-status", "args": ""},
        {"tool": "agent_compile_wave", "params": {"approve": ["drafts"]}},
        {"toolCall": {"toolName": "edit", "input": {"path": "plan.yaml"}}},
        {"toolCall": {"toolName": "bash", "input": {"command": "sed -i s/x/y/ plan.yaml"}}},
        {"toolCall": {"toolName": "write", "input": {"path": "runs/x/run.yaml"}}},
        {"toolCall": {"toolName": "read", "input": {"path": "plan.yaml"}}},
        {"toolCall": {"toolName": "bash", "input": {"command": "git commit -am x"}}},
        {"toolCall": {"toolName": "bash", "input": {"command": "git log --oneline"}}},
        {"toolCall": {"toolName": "read", "input": {"path": ".env"}}},
        {"toolCall": {"toolName": "bash", "input": {"command": "cat ./.env"}}},
        {"toolCall": {"toolName": "bash", "input": {"command": "source .venv/bin/activate && ls"}}},
        {"env": "MAIL_DIR"},
        {"cmd": "agent-run", "args": ""},
        {"tool": "agent_merge", "params": {"action": "keep"}},
        {"tool": "agent_record_decision", "params": {"stage": "no-such-stage", "answer": {"a": 1}}},
        {"toolCall": {"toolName": "read", "input": {"path": "/home/u/.pi/agent/sessions/--w--/2026-01-01_a.jsonl"}}},
        {"toolCall": {"toolName": "bash", "input": {"command": "grep -c failed ~/.pi/agent/sessions/--w--/a.jsonl"}}},
        {"tool": "agent_sessions", "params": {"action": "list"}},
        {"systemPrompt": "You are an expert coding assistant operating inside pi, a coding agent harness. You help users by reading files, executing commands, editing code, and writing new files.\n\n<tools>x</tools>"},
        {"systemPrompt": "Some other opening.\n\n<tools>x</tools>"},
    ]
    report = drive(ext, ext.tmp / ".ext" / "agent.ts", env.b, steps)
    env.agent_ext_report = report
    assert (len(report["commands"]), len(report["tools"])) == (8, 13)


@skip_extensions
def test_agent_run_sends_the_protocol_core_with_its_prompt(env):
    """So a start needs no read of it (TASKS 75); the detours stay in their own files."""
    (prompt,) = [o["text"] for o in env.agent_ext_report["steps"][12]["out"] if o["kind"] == "prompt"]
    assert "## Every start" in prompt and "## Finish" in prompt, prompt[:300]
    assert "## Decisions and approvals" not in prompt and "## Asking the user" not in prompt


@skip_extensions
def test_session_files_are_read_through_the_sessions_tool(env):
    read, shell, listed = env.agent_ext_report["steps"][-5:-2]
    assert [read["blocked"], shell["blocked"]] == [True, True]
    assert "agent_sessions" in read["reason"]
    assert listed.get("blocked") in (False, None) and "error" not in listed


@skip_extensions
def test_agent_compile_tool(env):
    assert env.agent_ext_report["steps"][1]["result"]["ok"] is True


@skip_extensions
def test_plan_edit_guard_once_a_run_exists(env):
    blocked = [s["blocked"] for s in env.agent_ext_report["steps"][2:6]]
    assert blocked == [True, True, True, False]


@skip_extensions
def test_git_guard(env):
    blocked = [s["blocked"] for s in env.agent_ext_report["steps"][6:8]]
    assert blocked == [True, False]


@skip_extensions
def test_a_wave_launches_only_from_the_commit_it_was_compiled_from(ext):
    # TASKS 77: a `git switch` between compile and launch would run another branch's programs and gates
    agent = ext.tmp / "switched"
    compiled = agent / "runs" / "r1" / ".compiled"
    compiled.mkdir(parents=True)
    (agent / ".pi").mkdir()  # an agent folder: plan.yaml and .pi/
    (agent / "plan.yaml").write_text("goal: g\n", encoding="utf-8")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(agent)]
    for args in (["init", "-q", "-b", "main"], ["add", "plan.yaml"], ["commit", "-qm", "x"]):
        _subprocess.run([*git, *args], check=True)
    head = _subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    for wave, record in (("w1", {"commit": head}), ("w2", {"commit": "0" * 40}), ("w3", {})):  # w3: a released run's
        (compiled / f"{wave}.js").write_text("const W = {};\n", encoding="utf-8")
        (compiled / f"{wave}.json").write_text(json.dumps(record), encoding="utf-8")
    steps = [{"toolCall": {"toolName": "subagent", "input": {"async": True, "workflow": f"runs/r1/.compiled/{w}.js"}}}
             for w in ("w1", "w2", "w3")]
    steps.append({"toolCall": {"toolName": "subagent", "input": {"action": "close"}}})  # no workflow: not a launch
    report = drive(ext, ext.tmp / ".ext" / "agent.ts", agent, steps)
    assert [s["blocked"] for s in report["steps"]] == [False, True, False, False]
    assert "compiled from commit 00000000, but main" in report["steps"][1]["reason"], report["steps"][1]


@skip_extensions
def test_dotenv_guard(env):
    blocked = [s["blocked"] for s in env.agent_ext_report["steps"][8:11]]
    assert blocked == [True, True, False]


@skip_extensions
def test_extension_keeps_dotenv_out_of_pis_environment(env):
    # every child inherits pi's environment and shares its sandbox: values reach children through their tasks, and
    # secrets only the batcher's commands (run_compile.secrets_for)
    assert env.agent_ext_report["steps"][11]["env"] is None


@skip_extensions
def test_agent_run_activates_subagent(env):
    steps = env.agent_ext_report["steps"]
    assert "subagent" not in steps[0]["activeTools"]  # /agent-status launches nothing
    assert {"subagent", "bg_wait"} <= set(steps[12]["activeTools"]) and steps[12]["out"][0]["kind"] == "prompt"


@skip_extensions
def test_script_tools_pass_their_arguments(env):
    # refusals that show each tool's parameters reached its script as arguments
    merge, decision = env.agent_ext_report["steps"][13:15]
    assert merge["schemaOk"] and "is active" in merge["result"]["error"]
    assert decision["schemaOk"] and "no-such-stage" in json.dumps(decision["result"])


@skip_extensions
def test_change_branch_guard(env, ext):
    _subprocess.run(["python3", str(env.k / "run_steps.py"), "stop"], cwd=env.b, capture_output=True, check=False)
    _subprocess.run(["python3", str(env.k / "plan_edit.py"), "begin-change"], cwd=env.b, capture_output=True, check=False)
    steps = [
        {"toolCall": {"toolName": "edit", "input": {"path": "plan.yaml"}}},
        {"toolCall": {"toolName": "bash", "input": {"command": "cat plan.yaml"}}},
        {"toolCall": {"toolName": "write", "input": {"path": "runs/x/run.yaml"}}},
        {"toolCall": {"toolName": "edit", "input": {"path": ".env"}}},
        # how it was planned is part of the plan; the critic's record only agent_record_critique writes
        {"toolCall": {"toolName": "edit", "input": {"path": ".plan/questions.yaml"}}},
        {"toolCall": {"toolName": "write", "input": {"path": ".plan/critique.yaml"}}},
        {"toolCall": {"toolName": "bash", "input": {"command": "sed -i s/revise/pass/ .plan/critique.yaml"}}},
        {"toolCall": {"toolName": "read", "input": {"path": ".plan/critique.yaml"}}},
    ]
    report = drive(ext, ext.tmp / ".ext" / "agent.ts", env.b, steps)
    assert [s["blocked"] for s in report["steps"]] == [False, False, True, True, False, True, True, False]


@skip_extensions
def test_main_guard_when_the_branch_was_switched(env, ext):
    # between runs main changes only by a reviewed merge; the user switching away from a change
    # branch mid-change must not let the agent's next edits land on main
    branch = env.git("branch", "--show-current").stdout.strip()
    assert branch.startswith("revise/")
    env.git("switch", "-q", "main")
    try:
        steps = [
            {"toolCall": {"toolName": "write", "input": {"path": "scripts/extract_invoice_lines.py"}}},
            {"toolCall": {"toolName": "edit", "input": {"path": "gates/check_lines.py"}}},
            {"toolCall": {"toolName": "bash", "input": {"command": "uv add openpyxl"}}},
            {"toolCall": {"toolName": "read", "input": {"path": "gates/check_lines.py"}}},
        ]
        report = drive(ext, ext.tmp / ".ext" / "agent.ts", env.b, steps)
    finally:
        env.git("switch", "-q", branch)
    assert [s["blocked"] for s in report["steps"]] == [True, True, True, False]
    assert branch in report["steps"][0]["reason"]


@skip_extensions
def test_the_coding_assistant_line_is_replaced_and_the_rest_kept(env):
    swapped, untouched = env.agent_ext_report["steps"][-2:]
    assert swapped["systemPrompt"].startswith("You are the agent") and swapped["systemPrompt"].endswith("<tools>x</tools>")
    assert "coding assistant" not in swapped["systemPrompt"]
    assert untouched["systemPrompt"] is None  # Pi reworded its line: leave the prompt alone


# how pi-subagents' wake message starts (src/shared/parent-wake.js, PARENT_WAKE_TEXT)
WAKE = "Inspect subagent updates above. Answer pending supervisor requests within your authority."


@skip_extensions
def test_agent_auto_runs_the_waves_without_the_model(ext, tmp_path, run_py):
    # a ready one-wave plan: /agent-auto starts the run, launches its wave through the RPC, records it and hands
    # over only for the final report (one model turn, not one per step)
    from test_run_finish import TWO
    r = run_py([SKILL / "scripts" / "agent_scaffold.py", "auto", "--root", tmp_path, "--request", "count the lines"])
    assert r.data["ok"], r
    wf = tmp_path / "auto"
    (wf / "plan.yaml").write_text(TWO, encoding="utf-8")
    finish = {"toolCall": {"toolName": "agent_finish_wave", "input": {"wave": "w1"}}}
    compile_call = {"toolCall": {"toolName": "agent_compile_wave", "input": {}}}
    report = drive(ext, ext.tmp / ".ext" / "agent.ts", wf, [
        {"cmd": "agent-auto", "background": True}, {"sleep": 3000},
        # while its wave runs, a completion notice's turn (pi-subagents' wake message): told to do nothing, and its
        # run tools refused
        {"systemPrompt": "Some other opening.", "prompt": WAKE}, finish,
        {"wait": True},
        # handed over: the hold is gone at once, even in a turn already under way (the hand-over arrives as a
        # follow-up and starts none of its own: refused agent_compile_wave, a model once stopped after an approval)
        {"systemPrompt": "Some other opening.", "prompt": "Go on."}, compile_call,
        # but the wave's notice arriving after the hand-over needs nothing: w1 is recorded (run 2026-10-09-4)
        {"systemPrompt": "Some other opening.", "prompt": WAKE}, finish,
        # a run pi-subagents starts with a message of its own is a notice too; another custom message is not
        {"systemPrompt": "Some other opening.", "message": {"customType": "subagent-completion-unhandled"}},
        {"systemPrompt": "Some other opening.", "message": {"customType": "agent"}},
    ], extra_env={"FAKE_SUBAGENTS_RPC": str(HERE / "mock-subagents.mjs"), "FAKE_SUBAGENTS_SLOW": "6000"})
    steps = report["steps"]
    assert "one word" in steps[2]["systemPrompt"] and steps[3]["blocked"], steps[2:4]
    assert steps[5]["systemPrompt"] is None and not steps[6]["blocked"], steps[5:7]
    assert "already recorded wave w1" in steps[7]["systemPrompt"], steps[7]
    assert steps[8]["blocked"] and "already recorded wave w1" in steps[8]["reason"], steps[8]
    assert "already recorded wave w1" in steps[9]["systemPrompt"] and steps[10]["systemPrompt"] is None, steps[9:]
    out = [o for s in report["steps"] for o in s["out"]]  # a background command's output lands in later steps
    assert [o["status"] for o in out if o["kind"] == "spawn"] == ["done"], out
    (prompt,) = [o["text"] for o in out if o["kind"] == "prompt" and o["deliverAs"] == "followUp"]
    assert "to the end" in prompt and "run-protocol.md" not in prompt and "mission_close" in prompt, prompt
    shown = [o["text"] for o in out if o["kind"] == "shown"]
    assert shown[0].startswith("Wave w1 started") and shown[1].startswith("Wave w1 done"), shown
    assert "status: done" in next((wf / "runs").glob("*/run.yaml")).read_text(encoding="utf-8")


@skip_extensions
def test_agent_auto_hands_over_when_the_runner_dies_without_an_end(ext, tmp_path, run_py):
    # else it would poll forever, and its hold would refuse /agent-run
    from test_run_finish import TWO
    r = run_py([SKILL / "scripts" / "agent_scaffold.py", "lost", "--root", tmp_path, "--request", "count the lines"])
    assert r.data["ok"], r
    (tmp_path / "lost" / "plan.yaml").write_text(TWO, encoding="utf-8")
    report = drive(ext, ext.tmp / ".ext" / "agent.ts", tmp_path / "lost", [{"cmd": "agent-auto"}],
                   extra_env={"FAKE_SUBAGENTS_RPC": str(HERE / "mock-subagents.mjs"), "FAKE_SUBAGENTS_DEAD": "1"})
    (prompt,) = [o["text"] for o in report["steps"][0]["out"] if o["kind"] == "prompt"]
    assert "stopped without recording how it ended" in prompt, prompt
