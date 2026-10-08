"""`prometheus serve` (prometheus/serve.py) through FastAPI's test client: the agent and its generated form, starting a
run from uploads, its state, events and outputs, a run that waits for an answer, the token, the limit on runs, and runs
read back after a restart. pi is faked (test_frozen.fake_launch: the stage programs run directly)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from conftest import SKILL
from fastapi.testclient import TestClient
from test_frozen import fake_pi  # noqa: F401 (a fixture)
from test_release import make_agent

from prometheus import frozen, serve
from prometheus.cli import PrometheusError


@pytest.fixture
def agent(tmp_path, run_py, monkeypatch, fake_pi):  # noqa: F811
    """The counter agent released and loaded; its runs count the uploaded file's lines."""
    monkeypatch.setenv("PROMETHEUS_CACHE", str(tmp_path / "cache"))
    (tmp_path / "agents").mkdir()
    folder = make_agent(tmp_path / "agents", run_py)
    assert run_py([SKILL / "scripts" / "release.py", "tag", "--agent", folder]).data["ok"]
    return frozen.load_agent(str(folder))


def client(agent, **options):
    return TestClient(serve.make_app(agent, **options))


def start(c, approve=("save",), wait=True):
    return c.post("/runs", params={"wait": wait}, files={"source": ("lines.txt", b"a\nb\nc\n")}, data={"approve": list(approve)})


def test_the_agent_and_its_form_are_described(agent):
    c = client(agent)
    assert c.get("/health").json() == {"ok": True}
    shown = c.get("/").json()
    assert (shown["name"], shown["version"], shown["side_effects"]) == ("counter", "v1.0", ["save"])
    openapi = c.get("/openapi.json").json()
    schema = openapi["paths"]["/runs"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
    fields = openapi["components"]["schemas"][schema["$ref"].rsplit("/", 1)[-1]]["properties"]
    assert set(fields) == {"approve", "source"}  # report is an output: the server places it in the run
    assert openapi["info"]["title"] == "counter v1.0"


def test_a_run_from_an_upload_ends_with_its_output_to_download(agent):
    c = client(agent)
    r = start(c, wait=False)
    run_id = r.json()["id"]
    assert r.status_code == 202 and r.headers["location"] == f"/runs/{run_id}"
    events = c.get(f"/runs/{run_id}/events").text  # streams until the run stops
    assert "event: child" in events and "event: end" in events
    shown = c.get(f"/runs/{run_id}").json()
    assert (shown["state"], shown["ok"], shown["cost_usd"]) == ("complete", True, 0.001)
    assert shown["outputs"] == {"count_file": {"url": f"/runs/{run_id}/outputs/count_file", "ready": True}}
    assert c.get(shown["outputs"]["count_file"]["url"]).content == b"3\n"
    assert (agent.folder / "runs" / run_id / "inputs" / "source" / "lines.txt").read_bytes() == b"a\nb\nc\n"


def test_a_run_waits_for_its_side_effect_to_be_approved(agent):
    c = client(agent)
    paused = start(c, approve=()).json()
    assert paused["state"] == "input_required" and paused["answer"] == f"/runs/{paused['id']}/answer"
    assert [a["stage"] for a in paused["needs"]["approve"]] == ["save"]
    assert paused["needs"]["approve"][0]["writes"] == ["outputs/count.txt"]  # relative to the run: no server paths
    done = c.post(paused["answer"], params={"wait": True}, json={"approve": ["save"]}).json()
    assert (done["state"], done["ok"]) == ("complete", True)
    assert c.post(paused["answer"], json={"approve": ["save"]}).status_code == 409  # it waits for nothing now


def test_what_a_run_needs_is_checked_before_it_starts(agent, monkeypatch):
    c = client(agent)
    assert c.post("/runs", data={"source": "/etc/passwd", "approve": ["save"]}).status_code == 422  # a path is a file
    assert client(agent, max_runs=0).post("/runs", files={"source": ("a", b"x")}, data={"approve": ["save"]}).status_code == 429

    def refuse(*args, **kwargs):
        raise PrometheusError("counter v1.0 can't run: missing params: source", {"preflight": {"missing_params": ["source"]}})
    monkeypatch.setattr(frozen.Agent, "start", refuse)
    refused = c.post("/runs", files={"source": ("a", b"x")})
    assert refused.status_code == 400 and refused.json()["detail"]["needs"] == {"params": ["source"], "secrets_on_the_server": []}


def test_a_token_guards_everything_but_health(agent):
    c = client(agent, token="s3cret")
    assert c.get("/health").status_code == 200
    assert c.get("/").status_code == 401
    assert c.get("/", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    with pytest.raises(PrometheusError, match="needs a token"):
        serve.check_host("0.0.0.0", None)
    with pytest.raises(PrometheusError, match="needs a token"):
        serve.check_host("0.0.0.0", "")  # PROMETHEUS_SERVE_TOKEN= set but empty
    assert client(agent, token="").get("/").status_code == 200  # an empty token is none (loopback only)
    serve.check_host("127.0.0.1", None)


def test_runs_are_read_back_after_a_restart(agent):
    run_id = start(client(agent)).json()["id"]
    shown = client(agent).get(f"/runs/{run_id}").json()  # a new server: the run comes from its folder
    assert shown["state"] == "complete" and shown["outputs"]["count_file"]["ready"]
    assert client(agent).get("/runs/nope").status_code == 404


def test_a_body_over_the_limit_is_refused_chunked_too(agent):
    c = client(agent, max_upload_mb=1)
    big = b"x" * (2**20 + 10)
    assert c.post("/runs", files={"source": ("a", big)}).status_code == 413  # by its Content-Length
    form = b'--x\r\nContent-Disposition: form-data; name="source"; filename="a"\r\n\r\n' + big + b"\r\n--x--\r\n"
    chunks = (form[i:i + 2**16] for i in range(0, len(form), 2**16))  # no Content-Length: counted as it arrives
    assert c.post("/runs", content=chunks, headers={"Content-Type": "multipart/form-data; boundary=x"}).status_code == 413
