"""Sharing agents through git (prometheus/share.py), with bare repositories in folders as the remotes: share, install
(refreshed from this prometheus, its own code reviewed), pull, propose, the catalog."""
import subprocess

import pytest
from conftest import ROOT, SKILL, git_in
from test_agent_release import commit, make_agent

from prometheus import share
from prometheus.cli import PrometheusError

TEMPLATE_EXTENSION = ROOT / "templates" / "agent" / ".pi" / "extensions" / "agent" / "index.ts"


@pytest.fixture
def world(tmp_path, run_py, monkeypatch):
    """An author's released agent, shared to a remote (a bare repository), and a user whose agents folder is empty:
    the user's agents folder is the one set (PROMETHEUS_AGENTS)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    (tmp_path / "author").mkdir()
    author = make_agent(tmp_path / "author", run_py)
    assert run_py([SKILL / "scripts" / "agent_release.py", "tag", "--agent", author]).data["version"] == "v1.0"
    remote = tmp_path / "counter.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    monkeypatch.setenv("PROMETHEUS_AGENTS", str(author.parent))
    shared = share.share("counter", str(remote))
    monkeypatch.setenv("PROMETHEUS_AGENTS", str(tmp_path / "agents"))
    return author, remote, tmp_path / "agents", shared


def test_share_pushes_main_and_releases_and_refuses_what_looks_like_a_secret(world, monkeypatch):
    author, remote, _, out = world
    assert out["shared"] and out["releases"] == ["v1.0"]
    assert git_in(remote)("tag", "-l").stdout.split() == ["v1.0"]
    assert [path for path, _ in out["own_code"]] == ["scripts/count.py", "scripts/save.py"]
    (author / "scripts" / "keys.py").write_text('KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123"\n')
    commit(author)
    monkeypatch.setenv("PROMETHEUS_AGENTS", str(author.parent))
    with pytest.raises(PrometheusError, match="scripts/keys.py: looks like an API key"):
        share.share("counter", str(remote))
    (author / "scripts" / "keys.py").unlink()  # deleted, but still in the history a share pushes
    commit(author)
    with pytest.raises(PrometheusError, match="scripts/keys.py: looks like an API key or token, in commit"):
        share.share("counter", str(remote))


def test_an_agents_connectors_and_skills_are_its_own_code_to_review(world):
    # an MCP server's command runs and a skill instructs its children: both are reviewed like programs
    author = world[0]
    (author / ".pi" / "mcp.json").write_text('{"mcpServers": {"notes": {"command": "npx", "args": ["-y", "notes@1.0.0"]}}}\n')
    (author / ".pi" / "skills" / "tone").mkdir(parents=True)
    (author / ".pi" / "skills" / "tone" / "SKILL.md").write_text("---\nname: tone\ndescription: d\n---\nBe brief.\n")
    commit(author)
    own = [path for path, _ in share.describe(author)["own_code"]]
    assert own == [".pi/mcp.json", ".pi/skills/tone/SKILL.md", "scripts/count.py", "scripts/save.py"]  # not the skill copy


def test_install_replaces_what_prometheus_owns_and_shows_the_agents_own_code(world):
    author, remote, agents, _ = world
    (author / ".pi" / "extensions" / "agent" / "index.ts").write_text("// a changed extension: runs as soon as pi starts\n")
    commit(author)
    git_in(author)("push", "-q", "origin", "main")
    seen = []
    folder = share.install(str(remote), confirm=lambda info: seen.append(info) or True)
    assert folder == agents / "counter"
    assert (folder / ".pi" / "extensions" / "agent" / "index.ts").read_text() == TEMPLATE_EXTENSION.read_text()
    assert [path for path, _ in seen[0]["own_code"]] == ["scripts/count.py", "scripts/save.py"]
    assert seen[0]["side_effects"] == ["save"]
    assert git_in(folder)("log", "-1", "--format=%s").stdout.startswith(share.REFRESH_COMMIT)
    with pytest.raises(PrometheusError, match="exists"):
        share.install(str(remote))

    with pytest.raises(PrometheusError, match="not installed"):
        share.install(str(remote), slug="other", confirm=lambda info: False)
    assert not (agents / "other").exists()


def test_pull_shows_the_upstreams_commits_then_takes_them(world):
    author, remote, _, _ = world
    folder = share.install(str(remote))
    assert share.pull("counter")["updated"] is False  # only a local refresh on top: nothing new
    (author / "scripts" / "count.py").write_text("print('counted differently')\n")
    commit(author, "count differently")
    git_in(author)("push", "-q", "origin", "main")
    seen = []
    out = share.pull("counter", confirm=lambda info: seen.append(info) or True)
    assert out["updated"] and [c.split(" ", 1)[1] for c in seen[0]["commits"]] == ["count differently"]
    assert any(c.endswith("scripts/count.py") for c in seen[0]["own_code_changed"])
    assert (folder / "scripts" / "count.py").read_text() == "print('counted differently')\n"
    (folder / "notes.txt").write_text("mine\n")
    commit(folder, "a local change")
    with pytest.raises(PrometheusError, match="commits the upstream doesn't"):
        share.pull("counter")


def test_propose_pushes_a_ready_change_branch(world):
    _, remote, _, _ = world
    folder = share.install(str(remote))
    with pytest.raises(PrometheusError, match="propose a change branch"):
        share.propose("counter")
    git = git_in(folder)
    git("switch", "-q", "-c", "revise/2026-10-08-1")
    (folder / "scripts" / "save.py").write_text("print('saved better')\n")  # programs alone: no critic needed
    commit(folder, "save better")
    assert share.propose("counter")["branch"] == "revise/2026-10-08-1"
    assert "revise/2026-10-08-1" in git_in(remote)("branch", "--list").stdout
    (folder / "plan.yaml").write_text((folder / "plan.yaml").read_text().replace("Count the lines", "Count lines"))
    commit(folder, "an unreviewed plan change")
    with pytest.raises(PrometheusError, match="isn't ready to propose"):
        share.propose("counter")


def test_the_catalog_names_agents_to_install(world, tmp_path, monkeypatch):
    _, remote, agents, _ = world
    (tmp_path / "catalog.yaml").write_text(f"agents:\n  - name: line-counter\n    url: {remote}\n    description: counts lines\n")
    monkeypatch.setenv("PROMETHEUS_CATALOG", str(tmp_path / "catalog.yaml"))
    assert [a["name"] for a in share.read_catalog()] == ["line-counter"]
    assert share.install("line-counter") == agents / "line-counter"


def test_the_shipped_catalog_reads():
    assert (ROOT / "catalog.yaml").is_file()
    share.read_catalog()


def test_install_runs_nothing_of_the_agents_before_you_said_yes(world, monkeypatch):
    # uv lock resolves (and may build) the agent's packages: only once it is trusted
    _, remote, _, _ = world
    steps, refresh = [], share.refresh
    monkeypatch.setattr(share, "refresh", lambda folder, lock=True: steps.append(f"refresh lock={lock}") or refresh(folder, lock))
    share.install(str(remote), slug="checked", confirm=lambda info: steps.append("confirm") or True)
    assert steps == ["refresh lock=False", "confirm", "refresh lock=True"]
