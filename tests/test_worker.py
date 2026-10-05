"""A real worker (ui/worker.ts) in each sandbox, through the router: what it loads and how it talks.

The router tests check that a worker answers; these check what it can do once up, which no other test sees: your Pi
packages loaded (pi-subagents, whose RPC /agent-auto launches waves through; a read-only Pi state once stopped every
package loading, unnoticed), the agent's own commands, a command's output reaching the page, and a dialog becoming
a question the page answers. A probe extension in the test user's Pi state does what a model would, so no model is
called.
"""
import json
import urllib.request
from pathlib import Path

import pytest
from test_router import (  # noqa: F401  (backend: a fixture)
    SECRET,
    agent_home,
    backend,
    get,
    start,
    stop,
)

pytestmark = pytest.mark.xdist_group("worker")

SUBAGENTS = Path.home() / ".pi" / "agent" / "npm" / "node_modules" / "pi-subagents"
HEAD = {"X-Forwarded-User": "ada", "X-Proxy-Secret": SECRET}

PROBE = """
export default function (pi: any) {
  // pi-subagents' in-process RPC, as /agent-auto calls it
  pi.registerCommand("probe-rpc", { description: "test", handler: async (_args: string, ctx: any) => {
    const requestId = `probe-${Date.now()}`;
    const reply: any = await new Promise((resolve) => {
      const off = pi.events.on(`subagents:rpc:v1:reply:${requestId}`, (r: any) => (off(), resolve(r)));
      setTimeout(() => resolve({ success: false, error: { message: "no answer" } }), 15_000);
      pi.events.emit("subagents:rpc:v1:request", { version: 1, requestId, method: "ping", params: {} });
    });
    ctx.ui.notify(`rpc: ${reply.success ? "pong" : reply.error?.message}`, "info");
  } });
  // a dialog: the page gets it as a question and answers it
  pi.registerCommand("probe-ask", { description: "test", handler: async (_args: string, ctx: any) => {
    const v = await ctx.ui.select("Pick one", ["red", "blue"]);
    ctx.ui.notify(`picked: ${v}`, "info");
  } });
}
"""


@pytest.fixture
def worker(tmp_path, run_py, backend):  # noqa: F811
    if not SUBAGENTS.is_dir():
        pytest.skip("needs pi-subagents installed in ~/.pi/agent/npm")
    agent_home(tmp_path, run_py)
    pi = tmp_path / "ada" / "pi-agent"
    (pi / "settings.json").write_text(json.dumps({"packages": ["npm:pi-subagents"]}), encoding="utf-8")
    (pi / "trust.json").write_text("{}", encoding="utf-8")  # Pi locks it: a read-only folder broke that
    (pi / "extensions" / "probe.ts").write_text(PROBE, encoding="utf-8")
    proc, port = start(TRUST_USER_HEADER="x-forwarded-user", PROXY_SECRET=SECRET, USERS_ROOT=str(tmp_path), SANDBOX=backend)
    yield port
    stop(proc)


def run(port, text=None, resume=None):
    """One AG-UI run on the agent's conversation "new-t": its events, in order."""
    body = {"threadId": "t", "runId": "r", "messages": [{"id": f"m-{text}", "role": "user", "content": text}] if text else [],
            **({"resume": [resume]} if resume else {})}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/a/s/s/new-t/agent", method="POST", data=json.dumps(body).encode(),
                                 headers={"Host": "localhost", "Content-Type": "application/json", **HEAD})
    with urllib.request.urlopen(req, timeout=120) as r:
        frames = r.read().decode().split("\n\n")
    return [json.loads(f[6:]) for f in frames if f.startswith("data: ")]


def said(events):
    return "".join(e.get("delta", "") for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")


def test_a_worker_loads_your_packages_and_the_agents_own_commands(worker):
    code, body = get(worker, "/a/s/s/new-t/commands", HEAD, timeout=120)
    names = {c["name"] for c in json.loads(body)} if code == 200 else set()
    assert {"subagents", "agent-auto", "agent-run", "probe-rpc"} <= names, (code, body[:500])


def test_pi_subagents_answers_its_rpc_in_a_worker(worker):
    events = run(worker, "/probe-rpc")
    assert "rpc: pong" in said(events), events
    assert events[-1]["type"] == "RUN_FINISHED" and events[-1]["outcome"]["type"] == "success"


def test_a_dialog_becomes_a_question_the_page_answers(worker):
    first = run(worker, "/probe-ask")
    outcome = first[-1]["outcome"]
    assert outcome["type"] == "interrupt", first
    asked = outcome["interrupts"][0]
    assert asked["message"] == "Pick one" and asked["metadata"]["options"] == ["red", "blue"], asked
    then = run(worker, resume={"interruptId": asked["id"], "status": "resolved", "payload": {"value": "blue"}})
    assert "picked: blue" in said(then), then

