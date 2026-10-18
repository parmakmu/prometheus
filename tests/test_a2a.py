"""A2A on `prometheus serve` (prometheus/a2a.py): the Agent Card, messages to tasks, a task that waits for an answer,
streaming, the task routes and the spec's errors. pi is faked as in test_serve.py."""
import base64
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from test_run_release import fake_pi  # noqa: F401 (a fixture)
from test_serve import agent  # noqa: F401 (a fixture: the released counter agent)

from prometheus import serve

PUBLIC = "https://agents.example.com/counter"
FILE = {"raw": base64.b64encode(b"a\nb\nc\n").decode(), "filename": "lines.txt", "mediaType": "text/plain"}
APPROVE = {"data": {"approve": ["save"]}}


def client(agent, **options):  # noqa: F811
    return TestClient(serve.make_app(agent, public_url=PUBLIC, **options))


def message(*parts, context="ctx-1", task=None):
    return {"message": {"messageId": "m-1", "role": "ROLE_USER", "contextId": context, "parts": list(parts),
                        **({"taskId": task} if task else {})}}


def reason(response):
    return response.json()["error"]["details"][0]["reason"]


def test_the_agent_card_describes_the_agent_without_the_token(agent):  # noqa: F811
    c = client(agent, token="s3cret")
    card = c.get("/.well-known/agent-card.json").json()
    capabilities = card["capabilities"]
    assert (card["name"], card["version"]) == ("counter", "v1.0")
    assert (capabilities["streaming"], capabilities["pushNotifications"], capabilities["extendedAgentCard"]) == (True, False, False)
    [interface] = capabilities["extensions"]
    assert (interface["uri"], interface["required"]) == ("urn:prometheus:a2a:interface:v1", False)
    assert interface["params"]["params"] == {"source": {"type": "path", "required": True, "sent_as": "file"},
                                             "report": {"type": "path", "required": False, "sent_as": "placed_in_run"}}
    assert (interface["params"]["side_effects"], list(interface["params"]["outputs"])) == (["save"], ["count_file"])
    assert card["supportedInterfaces"] == [{"url": PUBLIC, "protocolBinding": "HTTP+JSON", "protocolVersion": "1.0"}]
    assert card["skills"][0]["id"] == "counter" and "source (required)" in card["skills"][0]["description"]
    assert "save" in card["skills"][0]["description"] and "report" not in card["skills"][0]["description"]
    assert card["securitySchemes"] == {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}}
    unauthenticated = c.post("/message:send", json=message(FILE, APPROVE))
    assert unauthenticated.status_code == 401 and reason(unauthenticated) == "UNAUTHENTICATED"


def test_a_message_becomes_a_task_whose_outputs_are_artifacts(agent):  # noqa: F811
    c = client(agent)
    r = c.post("/message:send", json=message(FILE, APPROVE))
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/a2a+json")
    task = r.json()["task"]
    assert (task["contextId"], task["status"]["state"]) == ("ctx-1", "TASK_STATE_COMPLETED")
    assert task["artifacts"] == [{"artifactId": "count_file", "name": "count_file",
                                  "parts": [{"text": "3\n", "mediaType": "text/plain"},  # small text: inline too
                                            {"url": f"{PUBLIC}/runs/{task['id']}/outputs/count_file", "filename": "count.txt"}]}]
    assert c.get(f"/runs/{task['id']}/outputs/count_file").content == b"3\n"
    assert c.get(f"/tasks/{task['id']}").json()["status"]["state"] == "TASK_STATE_COMPLETED"
    listed = c.get("/tasks", params={"contextId": "ctx-1"}).json()
    assert [t["id"] for t in listed["tasks"]] == [task["id"]] and listed["totalSize"] == 1
    assert c.get("/tasks", params={"contextId": "other"}).json()["tasks"] == []


def test_a_task_waits_for_input_and_a_message_on_it_answers(agent):  # noqa: F811
    c = client(agent)
    task = c.post("/message:send", json=message(FILE)).json()["task"]  # save not approved
    assert task["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
    text, data = task["status"]["message"]["parts"]
    assert "approve save" in text["text"] and data["data"]["needs"]["approve"][0]["writes"] == ["outputs/count.txt"]
    done = c.post("/message:send", json=message(APPROVE, task=task["id"])).json()["task"]
    assert (done["id"], done["status"]["state"]) == (task["id"], "TASK_STATE_COMPLETED")
    again = c.post("/message:send", json=message(APPROVE, task=task["id"]))
    assert again.status_code == 400 and reason(again) == "TASK_NOT_WAITING"


def test_the_stream_sends_the_task_then_updates_then_the_end(agent):  # noqa: F811
    lines = [json.loads(line[5:]) for line in client(agent).post("/message:stream", json=message(FILE, APPROVE)).text.splitlines()
             if line.startswith("data:")]
    assert list(lines[0]) == ["task"]
    assert {"text": "count: completed"} in [u["statusUpdate"]["status"]["message"]["parts"][0] for u in lines[1:-2]]
    assert lines[-2]["artifactUpdate"]["artifact"]["artifactId"] == "count_file"
    assert lines[-1]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"


def test_the_spec_errors(agent):  # noqa: F811
    c = client(agent)
    assert reason(c.post("/message:send", json=message({"url": "http://169.254.169.254/latest"}, APPROVE))) == "CONTENT_TYPE_NOT_SUPPORTED"
    assert reason(c.post("/message:send", json=message({"raw": "not base64!!"}, APPROVE))) == "INVALID_PARAMS"
    missing = c.get("/tasks/nope")
    assert missing.status_code == 404 and reason(missing) == "TASK_NOT_FOUND"
    task = c.post("/message:send", json=message(FILE, APPROVE)).json()["task"]
    assert reason(c.post(f"/tasks/{task['id']}:cancel")) == "TASK_NOT_CANCELABLE"
    assert reason(c.post(f"/tasks/{task['id']}:subscribe")) == "UNSUPPORTED_OPERATION"
    assert reason(c.get(f"/tasks/{task['id']}/pushNotificationConfigs")) == "PUSH_NOTIFICATION_NOT_SUPPORTED"
    assert reason(c.get("/extendedAgentCard")) == "EXTENDED_AGENT_CARD_NOT_CONFIGURED"
    assert reason(c.get(f"/tasks/{task['id']}", headers={"A2A-Version": "2.0"})) == "VERSION_NOT_SUPPORTED"
