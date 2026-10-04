"""prometheus_version.py names which Prometheus an agent's skill copy came from; refresh stamps and uses it."""
import subprocess
import sys

from conftest import SKILL

sys.path.insert(0, str(SKILL / "scripts"))
import prometheus_version as pv

SCAFFOLD = SKILL / "scripts" / "scaffold_agent.py"


def fake_prometheus(tmp_path):
    root = tmp_path / "prom"
    skill = root / ".pi" / "skills" / "agent"
    (skill / "scripts").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname = "prometheus"\nversion = "1.2.3"\n\n[tool.x]\nversion = "9"\n')
    (skill / "scripts" / "a.py").write_text("one\n")

    def git(*args):
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()

    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "x")
    return skill, git


def test_a_clean_tree_is_version_plus_commit_and_each_dirty_content_has_its_own_label(tmp_path):
    skill, git = fake_prometheus(tmp_path)
    sha = git("rev-parse", "--short", "HEAD")
    assert pv.current(skill) == f"1.2.3+{sha}"
    (skill / "scripts" / "a.py").write_text("two\n")
    first = pv.current(skill)
    assert first.startswith(f"1.2.3+{sha}.dirty-")
    (skill / "scripts" / "a.py").write_text("three\n")
    assert pv.current(skill) != first  # other content, other label
    (skill / "scripts" / "a.py").write_text("two\n")
    assert pv.current(skill) == first  # the same content, the same label


def test_a_commit_outside_the_skill_and_templates_keeps_the_version(tmp_path):
    # a docs or tests commit must not make every agent stale
    skill, git = fake_prometheus(tmp_path)
    before = pv.current(skill)
    (tmp_path / "prom" / "README.md").write_text("docs\n")
    git("add", "-A")
    git("commit", "-qm", "docs")
    assert pv.current(skill) == before
    (skill / "scripts" / "a.py").write_text("two\n")
    git("commit", "-qam", "skill")
    assert pv.current(skill) == f"1.2.3+{git('rev-parse', '--short', 'HEAD')}"


def test_a_stamped_copy_reports_its_stamp_and_a_foreign_project_has_none(tmp_path):
    skill, _ = fake_prometheus(tmp_path)
    (skill / pv.VERSION_FILE).write_text("0.9+abc\n")
    assert pv.current(skill) == "0.9+abc"
    (tmp_path / "prom" / "pyproject.toml").write_text('[project]\nname = "other"\nversion = "1"\n')
    (skill / pv.VERSION_FILE).unlink()
    assert pv.current(skill) is None


def test_refresh_stamps_the_version_names_it_in_the_commit_and_skips_a_current_agent(tmp_path, run_py):
    assert run_py([SCAFFOLD, "ver", "--root", tmp_path, "--request", "x"]).data["ok"]
    wf = tmp_path / "ver"
    version = pv.current()
    assert (wf / ".pi" / "skills" / "agent" / pv.VERSION_FILE).read_text().strip() == version
    assert pv.installed(wf) == version

    skipped = run_py([SCAFFOLD, "ver", "--root", tmp_path, "--refresh", "--if-stale"]).data
    assert skipped["ok"] and "already at" in skipped["skipped"] and skipped["prometheus_version"] == version

    (wf / ".pi" / "skills" / "agent" / pv.VERSION_FILE).write_text("0.0.1+old\n")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-C", str(wf), "commit", "-qam", "old"], check=True)
    refreshed = run_py([SCAFFOLD, "ver", "--root", tmp_path, "--refresh", "--if-stale"]).data
    assert refreshed["ok"] and refreshed["was"] == "0.0.1+old" and refreshed["prometheus_version"] == version
    subject = subprocess.run(["git", "-C", str(wf), "log", "-1", "--format=%s"], capture_output=True, text=True, check=True).stdout.strip()
    assert subject == f"Refresh from prometheus {version} (was 0.0.1+old)"
    # an explicit refresh (no --if-stale) always runs
    assert "skipped" not in run_py([SCAFFOLD, "ver", "--root", tmp_path, "--refresh"]).data
