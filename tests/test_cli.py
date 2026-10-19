"""The `prometheus` command (prometheus/cli.py): where settings come from, init, and how pi is started."""
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from prometheus import cli, settings, skill

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / ".pi" / "skills" / "agent" / "scripts"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A user with no prometheus settings: HOME, the config folder and the environment all fresh."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for name in ("PROMETHEUS_AGENTS", "PROMETHEUS_HOME"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_settings_come_from_flag_then_environment_then_config_then_default(home, monkeypatch):
    assert cli.get_setting("agents") == (home / "agents", "default")
    cli.write_config({"agents": str(home / "from-config")})
    assert cli.get_setting("agents") == (home / "from-config", "config")
    monkeypatch.setenv("PROMETHEUS_AGENTS", "~/from-env")
    assert cli.get_setting("agents") == (home / "from-env", "environment")
    assert cli.get_setting("agents", str(home / "from-flag")) == (home / "from-flag", "flag")


def test_every_folder_setting_reads_the_environment_then_the_config_file(home, monkeypatch):
    for name in ("PROMETHEUS_RELEASES", "PROMETHEUS_CACHE", "PROMETHEUS_CATALOG", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        monkeypatch.delenv(name, raising=False)
    s = settings.settings()
    assert (s.releases, s.cache, s.catalog) == (home / ".local/share/prometheus/releases", home / ".cache/prometheus", None)
    cli.write_config({"releases": str(home / "from-config"), "catalog": "~/catalog.yaml"})
    monkeypatch.setenv("PROMETHEUS_CACHE", "")  # empty is unset, not the current folder
    s = settings.settings()
    assert (s.releases, s.cache, s.catalog) == (home / "from-config", home / ".cache/prometheus", home / "catalog.yaml")


def test_pi_subagents_is_found_where_pi_keeps_it_or_in_the_agent_first(home, monkeypatch):
    # one place knows where pi-subagents is installed (pi_records.install): doctor and the launcher ask it
    def installed(root, version):
        folder = root / "npm" / "node_modules" / "pi-subagents"
        folder.mkdir(parents=True)
        (folder / "package.json").write_text(json.dumps({"version": version}))
        return folder
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(home / "pi"))
    pi_records = skill.pi_records
    assert pi_records.install() is None and cli.find_pi_subagents() is None
    global_ = installed(home / "pi", "0.79.0")
    assert pi_records.install() == global_ and cli.find_pi_subagents() == "0.79.0"
    project = installed(home / "agent" / ".pi", "0.80.0")
    assert pi_records.install(home / "agent") == project and pi_records.install() == global_


def test_init_without_a_terminal_writes_the_config_and_the_planner_folder(home, monkeypatch):
    monkeypatch.setattr(cli, "show_checks", lambda: True)
    cli.init(agents_flag=str(home / "work" / "agents"), home_flag=None, yes=True)
    config = tomllib.loads(settings.config_file().read_text())
    assert config == {"agents": str(home / "work" / "agents"), "home": str(home / ".prometheus")}
    assert (home / "work" / "agents").is_dir()
    pi_dir = home / ".prometheus" / ".pi"
    assert (pi_dir / "settings.json").read_text() == (ROOT / ".pi" / "settings.json").read_text()
    critic = pi_dir / "agents" / "plan-critic.md"
    assert critic.is_symlink() and critic.read_text() == (ROOT / ".pi" / "agents" / "plan-critic.md").read_text()
    cli.init(agents_flag=None, home_flag=None, yes=True)  # again: the config file's folders, nothing broken
    assert tomllib.loads(settings.config_file().read_text()) == config


def test_the_planner_starts_pi_in_its_folder_with_prometheus_files(home, monkeypatch):
    started = {}
    monkeypatch.setattr(cli, "start_pi", lambda cwd, args, env: started.update(cwd=cwd, args=args, env=env))
    monkeypatch.setenv("PROMETHEUS_AGENTS", str(home / "agents"))
    cli.start_planner(["--resume"])
    pi_dir = ROOT / ".pi"
    assert started["cwd"] == home / ".prometheus"
    assert started["args"] == [
        "-e", str(pi_dir / "extensions" / "prometheus" / "index.ts"), "--skill", str(pi_dir / "skills" / "agent"),
        "--append-system-prompt", str(pi_dir / "APPEND_SYSTEM.md"),
        "--append-system-prompt", f"The agents folder is {home / 'agents'}.", "--resume"]
    assert started["env"] == {"PROMETHEUS_AGENTS": str(home / "agents"), "PI_AGENT_PYTHON": sys.executable}


def test_an_agent_starts_in_its_folder_and_a_missing_one_is_refused(home, monkeypatch):
    started = {}
    monkeypatch.setattr(cli, "start_pi", lambda cwd, args, env: started.update(cwd=cwd, args=args))
    (home / "agents" / "mail").mkdir(parents=True)
    (home / "agents" / "mail" / "plan.yaml").write_text("status: draft\n")
    cli.start_agent("mail", ["-c"])
    assert started == {"cwd": home / "agents" / "mail", "args": ["-c"]}
    with pytest.raises(cli.PrometheusError, match="no agent 'other'"):
        cli.start_agent("other", [])


def test_check_shows_a_hand_edited_plans_errors_and_exits_1(home, monkeypatch, capsys):
    import shutil

    import typer
    monkeypatch.setenv("PROMETHEUS_AGENTS", str(home / "agents"))
    folder = home / "agents" / "invoice-check"
    shutil.copytree(ROOT / ".pi" / "skills" / "agent" / "examples" / "invoice-check", folder)
    with pytest.raises(typer.Exit) as ok:
        cli.check("invoice-check")
    assert ok.value.exit_code == 0 and "invoice-check: valid, status ready" in capsys.readouterr().out
    plan = folder / "plan.yaml"
    plan.write_text(plan.read_text().replace("    reads: [mail]\n", "    consumes: [mail]\n", 1))
    with pytest.raises(typer.Exit) as bad:
        cli.check("invoice-check")
    out = capsys.readouterr().out
    assert bad.value.exit_code == 1 and "unknown field 'consumes'" in out and "not valid" in out


def test_doctor_refuses_a_pi_or_pi_subagents_older_than_prometheus_needs(home, monkeypatch):
    assert cli.check_version("pi", "1.3.0") == ("1.3.0", None) and cli.check_version("pi", "1.10.0")[1] is None
    assert cli.check_version("pi", "1.0.1")[1] == "1.0.1 is too old: needs 1.3.0 or later"
    assert cli.check_version("pi-subagents", "0.75.0")[1] == "0.75.0 is too old: needs 0.79.0 or later"
    assert cli.check_version("pi", None) == (None, None)
    monkeypatch.setattr(cli, "run_quiet", lambda *cmd: "1.0.1" if cmd[0] == "pi" else "x")
    monkeypatch.setattr(cli, "find_pi_subagents", lambda: "0.79.0")
    assert cli.show_checks() is False


def test_no_agent_can_be_named_like_a_command():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import agent_scaffold
    finally:
        sys.path.remove(str(SCRIPTS))
    assert cli.COMMANDS <= agent_scaffold.RESERVED
    r = subprocess.run([sys.executable, str(SCRIPTS / "agent_scaffold.py"), "doctor", "--root", "/nonexistent"],
                       capture_output=True, text=True, check=False)
    assert r.returncode == 2 and "is a prometheus command" in r.stdout


def test_release_shows_the_check_and_tags_only_when_told(home, monkeypatch, capsys):
    calls = []
    check = {"interface": {"params": {"source": {"type": "path", "required": True}}, "outputs": {"count_file": {}},
                           "side_effects": ["save"]}, "revision": 1, "last_release": None, "model_stages": [],
             "changes": {"breaking": [], "other": ["first release"]}, "already_released": None, "problems": [],
             "next_version": "v1.0"}

    def fake(script, *args):
        calls.append(args[0])
        return check if args[0] == "check" else {"ok": True, "version": "v1.0", "commit": "abcdef123"}
    monkeypatch.setattr(cli, "run_script", fake)
    monkeypatch.setenv("PROMETHEUS_AGENTS", str(home / "agents"))
    (home / "agents" / "counter").mkdir(parents=True)
    (home / "agents" / "counter" / "plan.yaml").write_text("status: ready\n")
    with pytest.raises(cli.typer.Exit):  # no terminal to ask in, and no --yes
        cli.release("counter", breaking=False, yes=False)
    assert calls == ["check"]
    cli.release("counter", breaking=False, yes=True)
    assert calls == ["check", "check", "tag"] and "Released counter v1.0" in capsys.readouterr().out


def test_a_module_named_like_a_skill_script_is_refused_by_name(tmp_path):
    # the scripts are top-level modules (`utils`, ...): another program's own `utils`, imported first, is not taken for ours
    (tmp_path / "utils.py").write_text("")
    r = subprocess.run([sys.executable, "-c", "import utils, prometheus.skill"], cwd=tmp_path, capture_output=True, text=True, check=False,
                       env={**os.environ, "PYTHONPATH": f"{tmp_path}:{ROOT}"})
    assert r.returncode != 0 and "a module named 'utils' is already imported" in r.stderr, r.stderr
    ok = subprocess.run([sys.executable, "-c", "import prometheus.skill, utils; print(utils.__file__)"], cwd=tmp_path,
                        capture_output=True, text=True, check=False, env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert ok.returncode == 0 and ok.stdout.strip().endswith("scripts/utils.py"), ok.stderr
