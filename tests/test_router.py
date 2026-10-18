"""The web app's router: who may call it (host, origin, token or a login proxy's user header).
Runs the real router on a free port (SANDBOX=off unless a test asks for bubblewrap); then escapes from an agent's sandbox."""
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from conftest import ROOT

TOKEN = "t0k-for-tests"
SECRET = "proxy-secret-for-tests"
pytestmark = pytest.mark.skipif(shutil.which("node") is None or shutil.which("npm") is None, reason="needs node and npm")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start(**env):
    port = free_port()
    proc = subprocess.Popen(["node", "ui/router.ts"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env={**os.environ, "PORT": str(port), "PROMETHEUS_TOKEN": TOKEN, "SANDBOX": "off", **env})
    for _ in range(100):
        if proc.poll() is not None:
            pytest.skip(f"router did not start (Pi installed globally?): {proc.stdout.read()[:500]}")
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            return proc, port
        except OSError:
            time.sleep(0.1)
    stop(proc)
    pytest.fail("router did not listen")


def stop(proc):
    """SIGTERM, so the router stops its workers and podman containers (SIGKILL would leave them to the next start)."""
    proc.terminate()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="module")
def router():
    proc, port = start()
    yield port
    stop(proc)


def get(port, path="/me", headers=None, timeout=5):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers={"Host": "localhost", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_the_token_lets_you_in(router):
    code, body = get(router, headers={"Authorization": f"Bearer {TOKEN}"})
    assert code == 200 and '"user"' in body, body


def test_no_or_a_wrong_token_is_refused(router):
    assert get(router)[0] == 403
    assert get(router, headers={"Authorization": "Bearer nope"})[0] == 403


def test_a_token_in_the_url_is_refused(router):
    # URLs end up in logs: the token goes as a header only
    assert get(router, path=f"/me?token={TOKEN}")[0] == 403


def test_another_website_is_refused(router):
    code, body = get(router, headers={"Authorization": f"Bearer {TOKEN}", "Origin": "https://evil.example"})
    assert code == 403 and "origin" in body


def test_an_unknown_host_name_is_refused(router):
    # DNS rebinding: a page on another name resolving to 127.0.0.1
    code, body = get(router, headers={"Authorization": f"Bearer {TOKEN}", "Host": "attacker.example"})
    assert code == 403 and "host" in body


def test_behind_a_login_proxy_the_user_header_names_you():
    proc, port = start(TRUST_USER_HEADER="x-forwarded-user", PROXY_SECRET=SECRET)
    try:
        code, body = get(port, headers={"X-Forwarded-User": "ada lovelace", "X-Proxy-Secret": SECRET})
        assert code == 200 and "ada_lovelace" in body, body
        assert get(port, headers={"X-Proxy-Secret": SECRET})[0] == 401
        # an agent's sandbox reaches the router on loopback too: a user header without the proxy's secret is refused
        assert get(port, headers={"X-Forwarded-User": "ada lovelace"})[0] == 403
    finally:
        stop(proc)


def test_an_agents_params_are_shown_and_set_but_never_a_secret(tmp_path, run_py):
    from conftest import SKILL
    home = tmp_path / "ada" / "prometheus"
    (home / ".pi" / "skills").mkdir(parents=True)
    (home / ".pi" / "APPEND_SYSTEM.md").write_text("", encoding="utf-8")
    shutil.copytree(SKILL, home / ".pi" / "skills" / "agent")  # the router runs prometheus's scripts, not the agent's
    assert run_py([SKILL / "scripts" / "agent_scaffold.py", "p", "--root", home / "agents", "--request", "x"]).data["ok"]
    plan = home / "agents" / "p" / "plan.yaml"
    text = plan.read_text(encoding="utf-8")
    assert "\nparams: {}\n" in text
    plan.write_text(text.replace("\nparams: {}\n", "\nparams:\n  src:\n    description: input folder\n  key:\n    description: api key\n    type: secret\n"), encoding="utf-8")
    proc, port = start(TRUST_USER_HEADER="x-forwarded-user", PROXY_SECRET=SECRET, USERS_ROOT=str(tmp_path))
    try:
        who = {"X-Forwarded-User": "ada", "X-Proxy-Secret": SECRET}
        code, body = get(port, "/a/p/params", who)
        assert code == 200 and '"name":"key"' in body and '"set":false' in body, body
        req = urllib.request.Request(f"http://127.0.0.1:{port}/a/p/params", method="POST", data=b'{"src": "/data/in"}',
                                     headers={"Host": "localhost", **who})
        assert urllib.request.urlopen(req, timeout=10).status == 200
        assert '"value":"/data/in"' in get(port, "/a/p/params", who)[1]
        req = urllib.request.Request(f"http://127.0.0.1:{port}/a/p/params", method="POST", data=b'{"key": "s3cret"}',
                                     headers={"Host": "localhost", **who})
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(req, timeout=10)
        assert refused.value.code == 400 and "s3cret" not in (home / "agents" / "p" / ".env").read_text(encoding="utf-8")
        # the agent's own copy of the script is not what runs: the agent's model can write it
        (home / "agents" / "p" / ".pi" / "skills" / "agent" / "scripts" / "plan_edit.py").write_text("print('{\"ok\": true, \"params\": [\"pwned\"]}')\n", encoding="utf-8")
        assert "pwned" not in get(port, "/a/p/params", who)[1]
    finally:
        stop(proc)


def test_a_conversation_shows_before_its_worker_has_started(tmp_path, run_py):
    # the page's first request is a sync; while Pi loads (~2 s) the router answers it from the session file
    import json
    import time

    from conftest import SKILL
    home = tmp_path / "ada" / "prometheus"
    (home / ".pi").mkdir(parents=True)
    (home / ".pi" / "APPEND_SYSTEM.md").write_text("", encoding="utf-8")
    assert run_py([SKILL / "scripts" / "agent_scaffold.py", "h", "--root", home / "agents", "--request", "x"]).data["ok"]
    agent = home / "agents" / "h"
    (agent / ".pi" / "sessions").mkdir(parents=True, exist_ok=True)
    entries = [
        {"type": "session", "version": 3, "id": "s1", "timestamp": "2026-10-05T09:00:00.000Z", "cwd": str(agent)},
        {"type": "message", "id": "e1", "parentId": None, "timestamp": "2026-10-05T09:00:01.000Z",
         "message": {"role": "user", "content": [{"type": "text", "text": "hello"}]}},
        {"type": "message", "id": "e2", "parentId": "e1", "timestamp": "2026-10-05T09:00:02.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "hi there"}]}},
    ]
    (agent / ".pi" / "sessions" / "2026-10-05T09-00-00-000Z_s1.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    proc, port = start(TRUST_USER_HEADER="x-forwarded-user", PROXY_SECRET=SECRET, USERS_ROOT=str(tmp_path))
    try:
        sync = json.dumps({"threadId": "t", "runId": "r", "messages": [], "tools": [], "context": [], "state": {}, "forwardedProps": {}}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/a/h/agent", method="POST", data=sync,
                                     headers={"Host": "localhost", "X-Forwarded-User": "ada", "X-Proxy-Secret": SECRET, "Content-Type": "application/json"})
        began = time.monotonic()
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read().decode()
        took = time.monotonic() - began
        snapshot = next(json.loads(line[6:]) for line in body.splitlines() if "MESSAGES_SNAPSHOT" in line)
        assert [(m["role"], m["content"]) for m in snapshot["messages"]] == [("user", "hello"), ("assistant", "hi there")]
        assert took < 1.0, f"{took:.2f} s: answered by the worker, not from the file"
    finally:
        stop(proc)


def test_a_login_proxy_without_a_secret_does_not_start():
    port = free_port()
    r = subprocess.run(["node", "ui/router.ts"], cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
                       env={**os.environ, "PORT": str(port), "SANDBOX": "off", "TRUST_USER_HEADER": "x-forwarded-user"})
    if "No Pi at" in r.stderr:
        pytest.skip("Pi is not installed globally")
    assert r.returncode == 1 and "PROXY_SECRET" in r.stderr


# --- escapes from an agent's sandbox (bubblewrap) -------------------------------------------------------------------

def agent_home(tmp_path, run_py, slug="s"):
    """USERS_ROOT/ada with prometheus (its skill), its own Pi state and agent `slug`."""
    from conftest import SKILL
    home = tmp_path / "ada" / "prometheus"
    (home / ".pi" / "skills").mkdir(parents=True)
    (home / ".pi" / "APPEND_SYSTEM.md").write_text("", encoding="utf-8")
    shutil.copytree(SKILL, home / ".pi" / "skills" / "agent")
    (tmp_path / "ada" / "pi-agent" / "extensions").mkdir(parents=True)
    assert run_py([SKILL / "scripts" / "agent_scaffold.py", slug, "--root", home / "agents", "--request", "x"]).data["ok"]
    return home / "agents" / slug


@pytest.fixture(params=["bwrap", "podman"])
def backend(request):
    """Each sandbox the router can build (SANDBOX): every escape test runs against both; skipped where one is missing."""
    if shutil.which(request.param) is None:
        pytest.skip(f"needs {request.param}")
    if request.param == "podman" and subprocess.run(["podman", "info"], capture_output=True, check=False).returncode:
        pytest.skip("podman doesn't answer (podman info)")
    return request.param


def in_sandbox(tmp_path, slug, command, backend):
    """Run `command` (sh) in agent `slug`'s sandbox, as the router builds it."""
    r = subprocess.run(["node", "ui/router.ts", "sandbox-run", "ada", f"a/{slug}", "--", "sh", "-c", command], cwd=ROOT,
                       capture_output=True, text=True, timeout=120, check=False, env={**os.environ, "USERS_ROOT": str(tmp_path), "SANDBOX": backend})
    if "No Pi at" in r.stdout + r.stderr:
        pytest.skip("Pi is not installed globally")
    return r


def test_an_agent_cannot_plant_code_that_runs_outside_its_sandbox(tmp_path, run_py, backend):
    agent = agent_home(tmp_path, run_py)
    pi = Path.home() / ".pi" / "agent"  # where the sandbox mounts the user's Pi state
    planted = {
        "a global Pi extension": pi / "extensions" / "x.ts",
        "its own extension": agent / ".pi" / "extensions" / "agent" / "index.ts",
        "its project settings": agent / ".pi" / "settings.json",
        "a subagent profile": agent / ".pi" / "agents" / "x.md",
        "git's config": agent / ".git" / "config",
        "a git hook": agent / ".git" / "hooks" / "post-commit",
    }
    # what a run writes stays writable (and the sandbox starts at all)
    for f in (agent / "plan.yaml", agent / ".pi" / "sessions" / "x.jsonl", agent / ".pi" / "APPEND_SYSTEM.md", pi / "sessions" / "x"):
        r = in_sandbox(tmp_path, "s", f"echo x >> '{f}'", backend)
        assert r.returncode == 0, f"{f}: {r.stderr}"
    for what, f in planted.items():
        r = in_sandbox(tmp_path, "s", f"echo x >> '{f}'", backend)
        assert r.returncode != 0 and "Read-only file system" in r.stderr, f"{what} ({f}): {r.returncode} {r.stderr}"
    assert not (tmp_path / "ada" / "pi-agent" / "extensions" / "x.ts").exists()
    # Pi's settings in the sandbox are a copy: a change there (say, adding an extension) never reaches the user's own
    real = tmp_path / "ada" / "pi-agent" / "settings.json"
    real.write_text('{"packages": []}\n', encoding="utf-8")
    before = real.read_text(encoding="utf-8")
    assert in_sandbox(tmp_path, "s", f"echo '{{\"extensions\": [\"/evil.ts\"]}}' > '{pi / 'settings.json'}'", backend).returncode == 0
    assert real.read_text(encoding="utf-8") == before
    # trust.json (which folders' extensions run) read-only, yet Pi can lock it: it makes trust.json.lock beside it,
    # and a read-only folder stopped it loading any package (pi-subagents, so /agent-auto) in every worker
    (tmp_path / "ada" / "pi-agent" / "trust.json").write_text("{}", encoding="utf-8")
    r = in_sandbox(tmp_path, "s", f"mkdir '{pi / 'trust.json.lock'}' && ! touch '{pi / 'trust.json'}' && touch '{pi / 'new-file'}'", backend)
    assert r.returncode == 0, r.stderr
    assert not (tmp_path / "ada" / "pi-agent" / "trust.json.lock").exists() and not (tmp_path / "ada" / "pi-agent" / "new-file").exists()


def test_the_router_runs_an_agents_git_in_its_sandbox(tmp_path, run_py, backend):
    # git runs the command an agent's .git/config names (core.fsmonitor) on `git diff`: in the sandbox it can't reach
    # the host (its /tmp is its own)
    agent = agent_home(tmp_path, run_py)
    pwned = tmp_path / "pwned"
    subprocess.run(["git", "-C", str(agent), "config", "core.fsmonitor", f"touch {pwned}; false"], check=True)
    proc, port = start(TRUST_USER_HEADER="x-forwarded-user", PROXY_SECRET=SECRET, USERS_ROOT=str(tmp_path), SANDBOX=backend)
    try:
        code, body = get(port, "/a/s/changes", {"X-Forwarded-User": "ada", "X-Proxy-Secret": SECRET})
        assert code == 200, body
        assert not pwned.exists()
    finally:
        stop(proc)


def test_an_agents_attachment_goes_to_your_files_folder_and_follows_no_link(tmp_path, run_py):
    agent = agent_home(tmp_path, run_py)
    attached = tmp_path / "ada" / "files" / "s"  # your files folder, under the agent's name: not the agent's folder
    outside = tmp_path / "outside"
    outside.mkdir()
    attached.mkdir(parents=True)
    (attached / "link").symlink_to(outside)
    (attached / "dangling").symlink_to(outside / "new.txt")
    head = {"X-Forwarded-User": "ada", "X-Proxy-Secret": SECRET}
    proc, port = start(TRUST_USER_HEADER="x-forwarded-user", PROXY_SECRET=SECRET, USERS_ROOT=str(tmp_path))
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/a/s/upload?unique=1&path=mail.eml", method="PUT", data=b"hi",
                                     headers={"Host": "localhost", **head})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert json.loads(r.read())["path"] == str(attached / "mail.eml")
        assert not (agent / "uploads").exists()
        code, body = get(port, "/files?dir=s", head)
        assert code == 200 and "mail.eml" in body, body
        for target in ("link/x.txt", "link/deep/x.txt", "dangling"):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/a/s/upload?path={target}", method="PUT", data=b"payload",
                                         headers={"Host": "localhost", "X-Forwarded-User": "ada", "X-Proxy-Secret": SECRET})
            with pytest.raises(urllib.error.HTTPError) as refused:
                urllib.request.urlopen(req, timeout=10)
            assert refused.value.code == 400, target
        assert list(outside.iterdir()) == []
    finally:
        stop(proc)


def test_the_planner_has_inputs_and_the_page_sees_only_them(tmp_path, run_py):
    home = agent_home(tmp_path, run_py).parent.parent
    (home / "inputs").mkdir()
    (home / "inputs" / "away").symlink_to(home / ".pi")
    head = {"X-Forwarded-User": "ada", "X-Proxy-Secret": SECRET}
    proc, port = start(TRUST_USER_HEADER="x-forwarded-user", PROXY_SECRET=SECRET, USERS_ROOT=str(tmp_path))
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/p/upload?unique=1&path=sample.txt", method="PUT", data=b"hi",
                                     headers={"Host": "localhost", **head})
        with urllib.request.urlopen(req, timeout=10) as r:
            assert json.loads(r.read())["path"] == str(home / "inputs" / "sample.txt")
        code, body = get(port, "/p/tree?dir=inputs", head)
        assert code == 200 and "sample.txt" in body, body
        assert get(port, "/p/download?path=inputs/sample.txt", head) == (200, "hi")
        for bad in ("/p/tree?dir=", "/p/tree?dir=.pi", "/p/tree?dir=inputs/../.pi", "/p/tree?dir=inputs/away",
                    "/p/download?path=.pi/APPEND_SYSTEM.md", "/p/download?path=inputs/away/APPEND_SYSTEM.md"):
            assert get(port, bad, head)[0] == 404, bad
    finally:
        stop(proc)


def call(port, path, method="GET", body=None, headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Host": "localhost", "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_an_api_key_calls_its_own_agents_api_and_nothing_else(tmp_path, run_py):
    agent_home(tmp_path, run_py)
    agent_home_also = tmp_path / "ada" / "prometheus" / "agents"
    assert run_py([ROOT / ".pi/skills/agent/scripts/agent_scaffold.py", "t", "--root", agent_home_also, "--request", "y"]).data["ok"]
    head = {"X-Forwarded-User": "ada", "X-Proxy-Secret": SECRET}
    proc, port = start(TRUST_USER_HEADER="x-forwarded-user", PROXY_SECRET=SECRET, USERS_ROOT=str(tmp_path))
    try:
        code, made = call(port, "/a/s/api-keys", "POST", {"label": "ci"}, head)
        assert code == 200 and made["key"].startswith("pak."), made
        assert "hash" not in json.dumps(call(port, "/a/s/api-keys", headers=head)[1])
        assert made["key"] not in (tmp_path / "ada" / "api-keys.json").read_text(encoding="utf-8")  # only its hash
        key = {"Authorization": f"Bearer {made['key']}"}
        code, card = call(port, "/a/s/api", headers=key)
        assert code == 200 and card["agent"] == "s" and "params" in card and "next" not in card, card
        # a draft plan: no run until it is ready
        assert call(port, "/a/s/api/runs", "POST", {"request": "go"}, key)[0] == 409
        for path in ("/a/t/api", "/a/s/tree?dir=", "/me", "/a/s/api-keys", "/p/sessions"):
            assert call(port, path, headers=key)[0] == 403, path
        assert call(port, "/a/s/api", headers={"Authorization": f"Bearer {made['key'][:-2]}xx"})[0] == 403
        assert call(port, f"/a/s/api-keys?id={made['id']}", "DELETE", headers=head)[0] == 200
        assert call(port, "/a/s/api", headers=key)[0] == 403
    finally:
        stop(proc)


def test_a_call_is_checked_against_what_the_agent_takes(tmp_path, run_py):
    agent = agent_home(tmp_path, run_py)
    plan = agent / "plan.yaml"
    plan.write_text(plan.read_text(encoding="utf-8").replace("status: draft", "status: ready").replace(
        "params: {}", "params:\n  mail_dir: {description: d}\n  token: {description: d, type: secret}"), encoding="utf-8")
    head = {"X-Forwarded-User": "ada", "X-Proxy-Secret": SECRET}
    proc, port = start(TRUST_USER_HEADER="x-forwarded-user", PROXY_SECRET=SECRET, USERS_ROOT=str(tmp_path))
    try:
        for body, why in (({}, "request"), ({"request": "go", "params": {"nosuch": "x"}}, "doesn't take"),
                          ({"request": "go", "params": {"token": "x"}}, "secrets"),
                          ({"request": "go", "files": {"../x": "aGk="}}, "files"),
                          ({"request": "go", "approve": ["nosuch"]}, "approve"),
                          ({"request": "go", "max_cost_usd": -1}, "max_cost_usd")):
            code, text = call(port, "/a/s/api/runs", "POST", body, head)
            assert code == 400 and why in text, (body, code, text)
        assert call(port, "/a/s/api/runs/c-000000000000", headers=head)[0] == 404
        assert call(port, "/a/s/api/runs", headers=head) == (200, [])
    finally:
        stop(proc)


def test_a_worker_starts_in_the_hardened_sandbox(tmp_path, run_py, backend):
    # Pi's state read-only, the agent's code read-only: Pi and pi-subagents must still start and answer
    agent_home(tmp_path, run_py)
    proc, port = start(TRUST_USER_HEADER="x-forwarded-user", PROXY_SECRET=SECRET, USERS_ROOT=str(tmp_path), SANDBOX=backend)
    try:
        code, body = get(port, "/a/s/status", {"X-Forwarded-User": "ada", "X-Proxy-Secret": SECRET}, timeout=60)
        assert code == 200 and '"model"' in body, body
    finally:
        stop(proc)


def test_a_runtime_that_cant_start_a_container_stops_the_router():
    # PODMAN_RUNTIME=runsc asks for gVisor: running without it would quietly drop that boundary
    if shutil.which("podman") is None or subprocess.run(["podman", "info"], capture_output=True, check=False).returncode:
        pytest.skip("needs podman")
    r = subprocess.run(["node", "ui/router.ts"], cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
                       env={**os.environ, "PORT": str(free_port()), "PROMETHEUS_TOKEN": TOKEN, "SANDBOX": "podman", "PODMAN_RUNTIME": "/bin/false"})
    if "No Pi at" in r.stderr:
        pytest.skip("Pi is not installed globally")
    assert r.returncode == 1 and "can't start a container" in r.stderr, r.stderr


def test_an_agent_reaches_only_the_hosts_allowed_for_it(tmp_path, run_py):
    # SANDBOX=podman: no route out but the egress proxy, which allows the base hosts and those approved for the agent
    if shutil.which("podman") is None or subprocess.run(["podman", "info"], capture_output=True, check=False).returncode:
        pytest.skip("needs podman")
    agent_home(tmp_path, run_py)
    (tmp_path / "ada" / "network").mkdir(parents=True)
    (tmp_path / "ada" / "network" / "s.json").write_text('{"allow": ["example.com"]}', encoding="utf-8")
    probe = """
import socket, urllib.request
try:
    socket.create_connection(("1.1.1.1", 443), timeout=5); print("direct:open")
except OSError: print("direct:closed")
for host in ("github.com", "example.com"):
    try: print(host + ":" + str(urllib.request.urlopen("https://" + host, timeout=20).status))
    except Exception as e: print(host + ":" + ("refused" if "403" in str(e) else "error " + str(e)[:60]))
"""
    r = in_sandbox(tmp_path, "s", f"python3 -c '{probe}'", "podman")
    assert "direct:closed" in r.stdout, r.stdout + r.stderr
    assert "github.com:refused" in r.stdout, r.stdout + r.stderr
    if "example.com:error" in r.stdout:
        pytest.skip("offline: the allowed host can't be reached")
    assert "example.com:200" in r.stdout, r.stdout + r.stderr


def test_what_an_agent_may_reach_is_yours_to_set(tmp_path, run_py):
    # the plan asks; you allow (kept in your folder, outside the sandbox); the proxy rereads it
    if shutil.which("podman") is None or subprocess.run(["podman", "info"], capture_output=True, check=False).returncode:
        pytest.skip("needs podman")
    agent = agent_home(tmp_path, run_py)
    plan = agent / "plan.yaml"
    plan.write_text(plan.read_text(encoding="utf-8") + "network: [api.github.com]\n", encoding="utf-8")
    proc, port = start(TRUST_USER_HEADER="x-forwarded-user", PROXY_SECRET=SECRET, USERS_ROOT=str(tmp_path), SANDBOX="podman")
    who = {"X-Forwarded-User": "ada", "X-Proxy-Secret": SECRET}
    try:
        code, body = get(port, "/a/s/network", who, timeout=30)
        assert code == 200 and '"enforced":true' in body and '"requested":["api.github.com"]' in body, body
        req = urllib.request.Request(f"http://127.0.0.1:{port}/a/s/network", method="POST", data=b'{"allow": ["api.github.com"]}',
                                     headers={"Host": "localhost", **who})
        assert urllib.request.urlopen(req, timeout=10).status == 200
        assert '"allowed":["api.github.com"],"requested":[]' in get(port, "/a/s/network", who, timeout=30)[1]
        assert "api.github.com" in (tmp_path / "ada" / "network" / "s.json").read_text(encoding="utf-8")
        bad = urllib.request.Request(f"http://127.0.0.1:{port}/a/s/network", method="POST", data=b'{"allow": ["x; rm -rf /"]}',
                                     headers={"Host": "localhost", **who})
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(bad, timeout=10)
        assert refused.value.code == 400
    finally:
        stop(proc)
