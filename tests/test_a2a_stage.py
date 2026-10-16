"""A stage that calls another agent over A2A (pattern: a2a): the validator's rules, the compiled batcher item, and
batch_tool.py's a2a op calling a real `prometheus serve` (the counter agent, pi faked) over HTTP."""
import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest
from conftest import SKILL
from ruamel.yaml import YAML

sys.path.insert(0, str(SKILL / "scripts"))
from compile_wave import Compiler
from validate_plan import validate

CALLER = """\
format: 1
revision: 1
status: draft
request: count the lines with the counter agent
goal: Count a file's lines with the counter agent.
done_when:
  - counted/ holds the counter agent's count.
params:
  lines_file:
    description: The file to count
  counter_url:
    description: The counter agent's address
    type: text
  counter_token:
    description: The counter agent's token
    type: secret
inputs:
  - id: lines
    kind: file
    path: ${lines_file}
agents:
  checked: test
  available: [batcher]
stages:
  - id: call-counter
    title: Have the counter agent count and save
    kind:
      value: side_effect
      reason: The counter agent saves its count.
    pattern: a2a
    agent: batcher
    reads: [lines]
    a2a:
      url: ${counter_url}
      files: {source: lines}
      approve: [save]
      token: ${counter_token}
    writes:
      - id: counted
        path: counted
    done_when: counted/ holds the count
"""


def plan(**changes):
    data = YAML(typ="safe").load(CALLER)
    stage = data["stages"][0]
    for key, value in changes.items():
        if value is None:
            stage.pop(key, None)
        elif key == "a2a":
            stage["a2a"] = {**stage["a2a"], **value}
        else:
            stage[key] = value
    return data


def errors(data):
    return " ".join(validate(data)[0])


def test_an_a2a_stage_is_valid():
    assert validate(plan())[0] == []


@pytest.mark.parametrize(("changes", "expected"), [
    ({"kind": {"value": "deterministic"}}, "make its kind side_effect"),
    ({"a2a": {"token": "s3cret-value"}}, "a2a token is a secret param"),
    ({"a2a": {"files": {"source": "elsewhere"}}}, "doesn't read it"),
    ({"a2a": None}, "pattern a2a needs `a2a:`"),
    ({"agent": "worker"}, "runs on batcher"),
    ({"writes": [{"id": "counted"}]}, "give writes a path"),
    ({"kind": {"value": "decision"}}, "can't ask anyone"),
])
def test_broken_a2a_stages(changes, expected):
    assert expected in errors(plan(**changes))


def test_it_compiles_to_a_batcher_call_with_the_token_as_a_secret(tmp_path):
    values = {"lines_file": "/data/lines.txt", "counter_url": "http://counter.example:8080", "counter_token": "set"}
    result = Compiler(tmp_path, plan(), values, tmp_path / "runs" / "r1", {}, {"call-counter"}).compile(
        {"id": "w1", "stages": ["call-counter"], "ends_with": "none"})
    [item] = result["spec"]["steps"][0]["layer"]
    request = json.loads(item["task"].removeprefix("BATCH_TOOL_REQUEST "))
    assert (item["agent"], "fix" in item) == ("batcher", False)
    assert {k: request[k] for k in ("op", "url", "files", "approve", "token", "secrets")} == {
        "op": "a2a", "url": "http://counter.example:8080", "files": {"source": "/data/lines.txt"}, "approve": ["save"],
        "token": "${COUNTER_TOKEN}", "secrets": ["COUNTER_TOKEN"]}
    assert request["out"] == str(tmp_path / "runs" / "r1" / "work" / "counted")


fastapi = pytest.importorskip("fastapi")
from test_frozen import fake_pi  # noqa: F401 (a fixture)
from test_serve import agent  # noqa: F401 (a fixture: the released counter agent)

from prometheus import frozen


@pytest.fixture
def served(agent):  # noqa: F811
    """The counter agent served over HTTP on a free port, with a token."""
    import uvicorn

    from prometheus import serve
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(serve.make_app(agent, token="t0k", public_url=url), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    yield url
    server.should_exit = True
    thread.join(5)


def call(request, **env):
    r = subprocess.run([sys.executable, str(SKILL / "scripts" / "batch_tool.py"), json.dumps(request)],
                       capture_output=True, text=True, env={**os.environ, **env}, check=False)
    return r.returncode, (json.loads(r.stdout) if r.stdout.strip() else {}), r.stderr


def test_batch_tool_calls_an_agent_and_downloads_its_outputs(served, tmp_path):
    (tmp_path / "lines.txt").write_text("a\nb\nc\n")
    request = {"op": "a2a", "url": served, "files": {"source": str(tmp_path / "lines.txt")}, "approve": ["save"],
               "token": "${COUNTER_TOKEN}", "out": str(tmp_path / "counted"), "timeout": 30}
    code, out, err = call(request, COUNTER_TOKEN="t0k")
    assert code == 0, err
    assert out["state"] == "TASK_STATE_COMPLETED" and (tmp_path / "counted" / "count.txt").read_text() == "3\n"
    code, out, err = call({**request, "approve": []}, COUNTER_TOKEN="t0k")
    assert code == 1 and "asks before it goes on" in err and "approve save" in err
    code, out, err = call(request, COUNTER_TOKEN="wrong")
    assert code == 1 and "HTTP 401" in err


def test_a_link_to_another_address_is_not_followed_with_the_token(served, tmp_path):
    (tmp_path / "lines.txt").write_text("a\n")
    elsewhere = served.replace("127.0.0.1", "localhost")  # the card names 127.0.0.1: another origin
    code, _, err = call({"op": "a2a", "url": elsewhere, "files": {"source": str(tmp_path / "lines.txt")}, "approve": ["save"],
                         "token": "${COUNTER_TOKEN}", "out": str(tmp_path / "counted"), "timeout": 30}, COUNTER_TOKEN="t0k")
    assert code == 1 and "on another address" in err


def test_a_blocking_send_leaves_the_server_answering(served, tmp_path, monkeypatch):
    # message:send waits for the run by default: that wait must not hold up every other request
    import urllib.request
    real_wait = frozen.Run.wait

    def slow_wait(self, timeout=None):
        time.sleep(2)
        return real_wait(self, timeout)
    monkeypatch.setattr(frozen.Run, "wait", slow_wait)
    body = json.dumps({"message": {"messageId": "m", "role": "ROLE_USER", "parts": [
        {"raw": "YQo=", "filename": "a.txt"}, {"data": {"approve": ["save"]}}]}}).encode()
    send = urllib.request.Request(f"{served}/message:send", data=body, method="POST",
                                  headers={"Authorization": "Bearer t0k", "Content-Type": "application/json"})
    sender = threading.Thread(target=lambda: urllib.request.urlopen(send, timeout=30).read())
    sender.start()
    time.sleep(0.5)
    began = time.monotonic()
    with urllib.request.urlopen(f"{served}/health", timeout=10) as r:
        assert r.status == 200
    assert time.monotonic() - began < 1
    sender.join(30)
