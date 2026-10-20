"""agent_capabilities.py: connectors and skills are added pinned, without credentials in git, and where a change can be
reviewed; registry searches turn into entries the tool can add as they are. No network: registries are stood in for."""
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import typer
from conftest import SKILL, git_in

sys.path.insert(0, str(SKILL / "scripts"))
import agent_capabilities as caps
import agent_repo

NO_PI = {**os.environ, "PATH": f"{Path(sys.executable).parent}:/usr/bin:/bin"}  # a written connector isn't started


def run(agent_dir, *args):
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "agent_capabilities.py"), *args, "--agent", str(agent_dir)],
                       capture_output=True, text=True, check=False, env=NO_PI)
    return json.loads(r.stdout)


def emitted(capsys, call):
    """What a command function printed before it exited (emit and refuse end with typer.Exit)."""
    with pytest.raises(typer.Exit):
        call()
    return json.loads(capsys.readouterr().out)


@pytest.fixture
def agent_dir(tmp_path):
    (tmp_path / "plan.yaml").write_text("format: 1\n", encoding="utf-8")
    git = git_in(tmp_path)
    git("init", "-q", "-b", "main")
    git("add", "-A")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return tmp_path


# --- connectors ----------------------------------------------------------------------------

@pytest.mark.parametrize("entry", [
    {"command": "npx", "args": ["-y", "@notionhq/notion-mcp-server@1.9.0"], "env": {"NOTION_TOKEN": "${NOTION_TOKEN}"}},
    {"command": "uvx", "args": ["mcp-server-fetch==2025.4.7"]},
    {"command": "uvx", "args": ["--from", "markitdown-mcp==0.0.1a4", "markitdown-mcp"]},
    {"command": "uvx", "args": ["--python", "3.12", "ruff@0.3.0"]},
    {"command": "docker", "args": ["run", "-i", "--rm", "ghcr.io/x/y:1"]},  # not a fetcher we pin-check
    {"url": "https://mcp.example.test/mcp", "headers": {"Authorization": "Bearer ${EXAMPLE_TOKEN}"}, "description": "d"},
    {"url": "http://localhost:8080/mcp"},
    {"command": "my-server", "env": {"AUTH_MODE": "none"}},  # a setting, not a credential
    {"url": "https://mcp.sentry.dev/mcp", "oauth": {"clientId": "c", "clientSecret": "${SENTRY_SECRET}", "scope": "a b"}},
])
def test_an_entry_that_is_pinned_and_keeps_credentials_out_is_accepted(entry):
    assert caps.find_entry_problems(entry) == []


@pytest.mark.parametrize("entry, problem", [
    ({"command": "npx", "args": ["-y", "@notionhq/notion-mcp-server"]}, "pin one"),
    ({"command": "npx", "args": ["-y", "server@latest"]}, "pin one"),
    ({"command": "npx", "args": ["-y", "server@^1.2.0"]}, "pin one"),
    ({"command": "uvx", "args": ["mcp-server-fetch"]}, "pin one"),
    ({"command": "uvx", "args": ["--from", "pkg>=1", "cmd"]}, "pin one"),
    ({"command": "npx bash -c 'x'"}, "one executable"),
    ({"command": "./server.sh"}, "one executable"),
    ({"command": "x", "url": "https://a.test"}, "either `command`"),
    ({"url": "http://mcp.example.test/mcp"}, "https://"),
    ({"url": "https://a.test", "headers": {"Authorization": "Bearer abcdefghijkl"}}, "Authorization: write credentials as ${NAME}"),
    ({"command": "x", "env": {"API_KEY": "abcdefghij"}}, "API_KEY: write credentials"),
    ({"command": "x", "args": ["--token", "ghp_" + "a" * 36]}, "an API key or token in args"),
    ({"command": "x", "env": {"TOKEN": "!gh auth token"}}, "runs a command"),
    ({"url": "https://a.test", "oauth": {"clientSecret": "abcdefghij"}}, "clientSecret"),
    ({"command": "x", "cwd": "/"}, "unknown field 'cwd'"),
    ({"url": "https://a.test", "type": "sse"}, "doesn't speak SSE"),
])
def test_an_entry_that_could_change_under_you_or_leak_is_refused(entry, problem):
    assert any(problem in p for p in caps.find_entry_problems(entry)), caps.find_entry_problems(entry)


def test_a_preview_writes_nothing_and_a_yes_writes_the_entry(agent_dir):
    entry = json.dumps({"command": "npx", "args": ["-y", "s@1.0.0"], "env": {"S_TOKEN": "${S_TOKEN}"}})
    preview = run(agent_dir, "connector", "notes", "--entry", entry, "--preview")
    assert preview["ok"] and preview["preview"] and preview["needs_env"] == ["S_TOKEN"] and preview["replaces"] is None
    assert not (agent_dir / ".pi" / "mcp.json").exists()
    assert "why the agent needs" in run(agent_dir, "connector", "notes", "--entry", entry)["error"]
    added = run(agent_dir, "connector", "notes", "--entry", entry, "--reason", "keep notes")
    assert added["ok"] and "mcp:notes" in added["how"] and "S_TOKEN" in added["how"]
    config = json.loads((agent_dir / ".pi" / "mcp.json").read_text(encoding="utf-8"))
    assert config == {"mcpServers": {"notes": json.loads(entry)}}


def test_a_server_of_the_same_name_is_replaced_and_others_kept(agent_dir):
    (agent_dir / ".pi").mkdir()
    (agent_dir / ".pi" / "mcp.json").write_text('{"mcpServers": {"my_docs": {"url": "https://a.test"}, "other": {"url": "https://b.test"}}, '
                                               '"autoEnableCodemode": false}', encoding="utf-8")
    entry = json.dumps({"url": "https://c.test"})
    assert run(agent_dir, "connector", "my-docs", "--entry", entry, "--preview")["replaces"] == {"url": "https://a.test"}  # - and _ alike
    assert run(agent_dir, "connector", "my-docs", "--entry", entry, "--reason", "r")["ok"]
    config = json.loads((agent_dir / ".pi" / "mcp.json").read_text(encoding="utf-8"))
    assert config == {"mcpServers": {"other": {"url": "https://b.test"}, "my-docs": {"url": "https://c.test"}}, "autoEnableCodemode": False}


def test_connectors_change_only_where_a_change_can_be_reviewed(agent_dir):
    (agent_dir / "runs" / "2026-01-01-1").mkdir(parents=True)
    (agent_dir / "runs" / "2026-01-01-1" / "run.yaml").write_text("id: 2026-01-01-1\nstatus: done\n", encoding="utf-8")
    r = run(agent_dir, "connector", "x", "--entry", '{"url": "https://a.test"}', "--preview")
    assert r["ok"] is False and "main is checked out and no run is active: connectors change only" in r["error"]
    git_in(agent_dir)("switch", "-q", "-c", "revise/2026-10-20-1")
    assert run(agent_dir, "connector", "x", "--entry", '{"url": "https://a.test"}', "--reason", "r")["ok"]


def test_a_bad_name_or_entry_is_refused(agent_dir):
    assert "can't name a server" in run(agent_dir, "connector", "a b", "--entry", "{}")["error"]
    assert "not JSON" in run(agent_dir, "connector", "a", "--entry", "{")["error"]


# --- find ---------------------------------------------------------------------------------

SERVER = {"server": {
    "name": "com.example/notion", "description": "Notion pages", "version": "1.0.2", "repository": {"url": "https://github.com/x/y"},
    "packages": [
        {"registryType": "pypi", "identifier": "example-notion", "version": "1.0.2", "transport": {"type": "stdio"},
         "environmentVariables": [{"name": "NOTION_TOKEN", "isSecret": True}, {"name": "LOG_LEVEL"}]},
        {"registryType": "oci", "identifier": "ghcr.io/x/y:1", "version": "1.0.2", "transport": {"type": "stdio"}},
    ],
    "remotes": [{"type": "streamable-http", "url": "https://mcp.example.test/mcp",
                 "headers": [{"name": "Authorization", "value": "Bearer {smithery_api_key}", "isSecret": True}]},
                {"type": "sse", "url": "https://mcp.example.test/sse"}],
}}


def test_a_registry_server_becomes_entries_that_can_be_added_as_they_are(monkeypatch):
    monkeypatch.setattr(caps, "fetch_json", lambda url, params=None: {"servers": [SERVER]})
    [found] = caps.find_connectors("notion", 5)
    assert [o["entry"] for o in found["options"]] == [
        {"command": "uvx", "args": ["example-notion==1.0.2"], "env": {"NOTION_TOKEN": "${NOTION_TOKEN}", "LOG_LEVEL": "${LOG_LEVEL}"}},
        {"url": "https://mcp.example.test/mcp", "headers": {"Authorization": "Bearer ${SMITHERY_API_KEY}"}},
    ]  # no container, no SSE
    assert [o["needs_env"] for o in found["options"]] == [["NOTION_TOKEN"], ["SMITHERY_API_KEY"]]
    assert all(caps.find_entry_problems(o["entry"]) == [] for o in found["options"])


def test_the_apps_own_server_comes_first_and_a_long_need_is_searched_word_by_word(monkeypatch):
    # the registry matches part of a server's name only; its order is by name, so third parties come first
    def server(name, status="active"):
        return {"server": {"name": name, "remotes": [{"type": "streamable-http", "url": f"https://{name.split('/')[0]}.test/mcp"}]},
                "_meta": {"io.modelcontextprotocol.registry/official": {"status": status}}}

    asked = []

    def fetch(url, params=None):
        asked.append(params["search"])
        if params["search"] != "notion":
            return {"servers": []}
        return {"servers": [server("ai.smithery/smithery-notion"), server("com.mcparmory/notion"),
                            server("com.example/notion-old", "deprecated"), server("com.notion/mcp")]}

    monkeypatch.setattr(caps, "fetch_json", fetch)
    found = caps.find_connectors("notion pages", 5)
    assert asked == ["notion pages", "notion", "pages"]
    assert [(c["name"], c["publisher"], c["vendor"]) for c in found] == [
        ("com.notion/mcp", "notion", True), ("ai.smithery/smithery-notion", "smithery", False), ("com.mcparmory/notion", "mcparmory", False)]
    asked.clear()
    caps.find_connectors("notion", 5)
    assert asked == ["notion"]  # a need that matched is not widened
    assert caps.name_publisher("io.github.makenotion/notion-mcp") == "makenotion"


def write_fake_pi(folder, answer):
    """A `pi` that answers `pi mcp list --json` with `answer`, and keeps the mcp.json it was given."""
    folder.mkdir(exist_ok=True)
    (folder / "pi").write_text(f"#!/bin/sh\ncp \"$PI_CODING_AGENT_DIR/mcp.json\" {folder}/given.json\npwd > {folder}/cwd\n"
                               f"echo '{json.dumps(answer)}'\n", encoding="utf-8")
    (folder / "pi").chmod(0o755)


def test_an_added_server_is_started_alone_to_list_its_tools(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    entry = {"command": "uvx", "args": ["mcp-server-time==2026.10.19"]}
    write_fake_pi(bin_dir, {"servers": [{"name": "time", "state": "connected", "tools": ["get_current_time", "convert_time"]}]})
    assert caps.list_server_tools("time", entry) == (["get_current_time", "convert_time"], None)
    assert json.loads((bin_dir / "given.json").read_text()) == {"mcpServers": {"time": entry}}  # nothing else started
    assert not (Path((bin_dir / "cwd").read_text().strip()) / ".pi").exists()  # outside any project
    write_fake_pi(bin_dir, {"servers": [{"name": "time", "state": "needs-auth", "tools": []}]})
    assert caps.list_server_tools("time", entry) == (None, "it needs a sign-in: ask the user to run /mcp login time")
    write_fake_pi(bin_dir, {"servers": [{"name": "time", "state": "failed", "error": "NOTION_TOKEN is not set"}]})
    assert "did not connect (failed: NOTION_TOKEN is not set)" in caps.list_server_tools("time", entry)[1]
    (bin_dir / "pi").unlink()
    assert caps.list_server_tools("time", entry) == (None, "pi is not on PATH")


def test_find_reports_a_registry_that_did_not_answer_beside_the_others(monkeypatch, capsys):
    def fetch(url, params=None):
        if "github" in url:
            raise caps.Unreachable("api.github.com answered 403")
        if "/-/v1/search" in url:
            return {"objects": [{"package": {"name": "pi-pdf", "version": "1.0.1", "description": "PDFs"}}]}
        return {"pi": {"skills": ["./skills"]}, "repository": {"url": "git+https://github.com/x/pi-pdf.git"}}

    monkeypatch.setattr(caps, "fetch_json", fetch)
    out = emitted(capsys, lambda: caps.find("pdf", kind="skill", limit=5))
    assert out["candidates"] == [{"from": "npm", "name": "pi-pdf", "description": "PDFs", "source": "npm:pi-pdf@1.0.1",
                                  "repository": "https://github.com/x/pi-pdf", "paths": ["skills"], "monthly_downloads": None}]
    assert out["unreachable"] == ["api.github.com answered 403"] and "never instructions" in out["note"]
    monkeypatch.setattr(caps, "fetch_json", lambda url, params=None: (_ for _ in ()).throw(caps.Unreachable("down")))
    assert "no registry answered" in emitted(capsys, lambda: caps.find("pdf", kind="skill", limit=5))["error"]


def test_a_package_is_looked_up_by_its_name_only(capsys):
    assert "by its name" in emitted(capsys, lambda: caps.find("convert pdf files", kind="package", limit=5))["error"]


# --- skills -------------------------------------------------------------------------------

def write_skill(folder, name="pdf-tools", description="Read PDFs.", files=None):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\nDo this.\n", encoding="utf-8")
    for rel, text in (files or {}).items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text, encoding="utf-8")
    return folder


def test_a_skill_is_read_with_the_code_it_carries(tmp_path):
    info = caps.read_skill(write_skill(tmp_path / "s", files={"scripts/fill.py": "print(1)", "REFERENCE.md": "x"}))
    assert info["name"] == "pdf-tools" and info["code"] == ["scripts/fill.py"]
    assert [f["path"] for f in info["files"]] == ["REFERENCE.md", "SKILL.md", "scripts/fill.py"]


@pytest.mark.parametrize("make, problem", [
    (lambda d: write_skill(d, name="PDF Tools"), "lowercase letters"),
    (lambda d: write_skill(d, name="agent"), "prometheus's own skill copy"),
    (lambda d: write_skill(d, description=""), "no description"),
    (lambda d: write_skill(d, files={"inner/SKILL.md": "x"}), "a second skill"),
    (lambda d: write_skill(d, files={"SOURCE.json": "{}"}), "its own SOURCE.json"),
    (lambda d: (write_skill(d), (d / "link").symlink_to("/etc/passwd")), "is a link"),
])
def test_a_skill_that_is_not_one_clean_folder_is_refused(tmp_path, capsys, make, problem):
    make(tmp_path / "s")
    assert problem in emitted(capsys, lambda: caps.read_skill(tmp_path / "s"))["error"]


def test_a_skill_installs_whole_and_updates_only_from_its_own_source(agent_dir, tmp_path, capsys):
    folder = write_skill(tmp_path / "src", files={"a.md": "one"})
    origin = {"source": "https://github.com/x/skills", "path": "pdf-tools", "pin": "a" * 40}
    dest = caps.install_skill(agent_dir, folder, caps.read_skill(folder), origin)
    assert dest == agent_dir / ".pi" / "skills" / "pdf-tools" and (dest / "a.md").read_text() == "one"
    assert json.loads((dest / "SOURCE.json").read_text()) == origin
    (folder / "a.md").write_text("two")
    caps.install_skill(agent_dir, folder, caps.read_skill(folder), {**origin, "pin": "b" * 40})  # the same source: updated
    assert (dest / "a.md").read_text() == "two" and not [p for p in dest.parent.iterdir() if p.name.startswith(".")]
    other = {**origin, "source": "npm:pi-pdf@1.0.1"}
    out = emitted(capsys, lambda: caps.install_skill(agent_dir, folder, caps.read_skill(folder), other))
    assert "already has a skill named 'pdf-tools', from https://github.com/x/skills" in out["error"]


@pytest.mark.parametrize("source, problem", [
    ("file:///tmp/repo", "neither an https URL"),
    ("ext::sh -c x", "neither an https URL"),
    ("git@github.com:x/y.git", "neither an https URL"),
    ("https://user:pw@github.com/x/y", "neither an https URL"),
    ("npm:pi-pdf@^1.0.0", "name one version"),
    ("npm:pi-pdf@latest", "name one version"),
])
def test_a_skill_comes_only_from_https_git_or_one_npm_version(agent_dir, source, problem):
    assert problem in run(agent_dir, "skill", source, "--preview")["error"]


def test_a_skill_installs_only_what_a_preview_showed(agent_dir):
    assert "preview the skill first" in run(agent_dir, "skill", "https://github.com/x/y", "--reason", "r")["error"]
    assert "why the agent needs" in run(agent_dir, "skill", "https://github.com/x/y", "--pin", "a" * 40)["error"]


def make_tarball(files):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w:gz") as tar:
        for name, text in files.items():
            info = tarfile.TarInfo(f"package/{name}")
            info.size = len(text.encode())
            tar.addfile(info, io.BytesIO(text.encode()))
    return data.getvalue()


def test_an_npm_skill_is_checked_against_the_integrity_npm_published(monkeypatch, tmp_path, capsys):
    tarball = make_tarball({"skills/pdf/SKILL.md": "---\nname: pdf\ndescription: d\n---\n"})
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(tarball).digest()).decode()
    url = "https://registry.npmjs.org/pi-pdf/-/pi-pdf-1.0.1.tgz"

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(caps.urllib.request, "urlopen", lambda request, timeout=None: Response(tarball))
    monkeypatch.setattr(caps, "fetch_json", lambda u, params=None: {"dist": {"tarball": url, "integrity": integrity}})
    assert caps.fetch_package("pi-pdf", "1.0.1", tmp_path / "a") == integrity
    assert caps.list_skill_folders(tmp_path / "a" / "package") == ["skills/pdf"]
    monkeypatch.setattr(caps, "fetch_json", lambda u, params=None: {"dist": {"tarball": url, "integrity": "sha512-AAAA"}})
    assert "doesn't match the integrity" in emitted(capsys, lambda: caps.fetch_package("pi-pdf", "1.0.1", tmp_path / "b"))["error"]
    monkeypatch.setattr(caps, "fetch_json", lambda u, params=None: {"dist": {"tarball": "https://evil.test/x.tgz", "integrity": integrity}})
    assert "no tarball" in emitted(capsys, lambda: caps.fetch_package("pi-pdf", "1.0.1", tmp_path / "c"))["error"]


def test_a_release_needs_a_run_of_its_connectors_and_skills_but_not_of_a_refresh(agent_dir):
    # built_from: what decides how the agent runs, with .pi/mcp.json and .pi/skills/ but not the skill copy
    git = git_in(agent_dir)

    def commit_with(rel, text):
        (agent_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (agent_dir / rel).write_text(text, encoding="utf-8")
        git("add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", rel)
        return agent_repo.built_from(agent_dir)

    first = agent_repo.built_from(agent_dir)
    assert commit_with(".pi/skills/agent/VERSION", "1") == first
    with_server = commit_with(".pi/mcp.json", '{"mcpServers": {}}')
    assert with_server != first
    assert commit_with(".pi/skills/tone/SKILL.md", "x") != with_server
