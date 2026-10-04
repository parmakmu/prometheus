"""Param values (workflow_params.py) and how the compiler uses them: where values
come from, how each type compiles, and that secrets are never written down.

One scenario, like test_workflow.py: values written by an earlier test are
read back by a later one, so these run in file order (pytest's default,
undisturbed by any randomizing plugin).
"""
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest
from conftest import SKILL

# module fixtures shared by the tests here: keep them on one worker
pytestmark = pytest.mark.xdist_group("params")

SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
import workflow_params as wp

spec = importlib.util.spec_from_file_location("compile_wave", SCRIPTS / "compile_wave.py")
cw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cw)

SPECS = {
    "mail_dir": {"description": "d", "type": "path"},
    "report_dir": {"description": "d", "default": "outputs"},
    "table": {"description": "d", "type": "text"},
    "api_token": {"description": "d", "type": "secret", "env": "TEST_API_TOKEN"},
}


@pytest.fixture(scope="module", autouse=True)
def clean_env():
    """These param names must come from the fixtures below, not whatever the
    shell running pytest happens to export."""
    for k in ("MAIL_DIR", "TABLE", "TEST_API_TOKEN", "REPORT_DIR"):
        os.environ.pop(k, None)
    yield
    for k in ("MAIL_DIR", "TABLE", "TEST_API_TOKEN", "REPORT_DIR"):
        os.environ.pop(k, None)


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("params")
    return types.SimpleNamespace(root=root)


def test_unset_nothing_but_the_default(env):
    env.v = wp.load(env.root, SPECS)
    assert env.v == {"mail_dir": None, "report_dir": "outputs", "table": None, "api_token": None}


def test_missing_lists_required_params_without_a_value(env):
    assert wp.missing(SPECS, env.v) == ["mail_dir", "table", "api_token"]


def test_set_values_writes_dotenv_under_the_variable_names(env):
    problems = wp.set_values(env.root, SPECS, {"mail_dir": "~/Mail", "table": "invoices"})
    assert problems == []
    env_text = (env.root / ".env").read_text(encoding="utf-8")
    assert "MAIL_DIR=" in env_text
    assert "TABLE=" in env_text


def test_set_values_refuses_a_secret(env):
    problems = wp.set_values(env.root, SPECS, {"api_token": "s3cret"})
    assert problems and "is a secret" in problems[0]
    assert "s3cret" not in (env.root / ".env").read_text(encoding="utf-8")


def test_set_values_refuses_an_undeclared_param(env):
    assert wp.set_values(env.root, SPECS, {"nope": "x"}) != []


def test_environment_wins_over_dotenv(env):
    with (env.root / ".env").open("a", encoding="utf-8") as fh:
        fh.write("TEST_API_TOKEN=from-file\n")
    os.environ["TABLE"] = "from-env"
    env.v = wp.load(env.root, SPECS)
    assert env.v["table"] == "from-env"


def test_dotenv_value_read(env):
    assert env.v["mail_dir"] == "~/Mail"


def test_a_secret_loads_as_secretstr(env):
    assert isinstance(env.v["api_token"], wp.SecretStr)
    assert env.v["api_token"].get_secret_value() == "from-file"


def test_a_run_records_a_secret_only_as_set(env):
    rec = wp.record(SPECS, env.v)
    assert rec["api_token"] == "(secret, set)"
    assert "from-file" not in str(rec)


def test_reload_without_the_environment_override(env):
    os.environ.pop("TABLE")
    env.v = wp.load(env.root, SPECS)  # TABLE from .env again
    env.run_dir = env.root / "runs" / "r1"


def test_resolve_path_expands_tilde(env):
    assert wp.resolve("mail_dir", SPECS["mail_dir"], "~/Mail", env.run_dir) == str(Path("~/Mail").expanduser())


def test_resolve_path_relative_resolves_inside_the_run(env):
    assert wp.resolve("report_dir", SPECS["report_dir"], "outputs", env.run_dir) == str(env.run_dir / "outputs")


def test_resolve_text_used_as_given(env):
    assert wp.resolve("table", SPECS["table"], "invoices", env.run_dir) == "invoices"


def test_resolve_secret_compiles_to_its_variable(env):
    assert wp.resolve("api_token", SPECS["api_token"], env.v["api_token"], env.run_dir) == "${TEST_API_TOKEN}"


def test_gate_text_verbatim_secret_as_var_never_its_value(env):
    plan = {"params": SPECS, "stages": []}
    c = cw.Compiler(env.root, plan, env.v, env.run_dir, {}, set())
    gate = c.gate_command("python3 ../gates/check.py ${table} ${api_token} ${mail_dir}")
    assert gate.endswith(f"check.py invoices ${{TEST_API_TOKEN}} {Path('~/Mail').expanduser()}")
    assert "from-file" not in gate


def test_an_unset_secret_is_reported_by_its_variable(env):
    plan = {"params": SPECS, "stages": []}
    c = cw.Compiler(env.root, plan, wp.load(env.root / "elsewhere", SPECS), env.run_dir, {}, set())
    c.gate_command("x ${api_token} ${table}")
    assert c.missing_secrets == {"TEST_API_TOKEN"}
    assert c.missing_params == {"table"}
