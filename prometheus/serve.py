"""`prometheus serve`: one released agent as an HTTP service (FastAPI), for whoever deploys it; it also speaks A2A
(a2a.py: the Agent Card at /.well-known/agent-card.json, /message:send, /tasks/…), on the same runs. One agent per
process: its environment holds that agent's secrets only, and each agent is deployed, pinned and scaled on its own
(several on one host: one process each, behind a proxy).

    GET    /health                     {"ok": true}
    GET    /                           the agent: version, params, outputs, side effects
    POST   /runs[?wait=true]           start a run (multipart form): 202 with the run, or 200 once it stops
    POST   /runs/<id>/answer[?wait=true]   answer a run waiting for one: {"approve": [...], "decisions": {stage: answer}}
    GET    /runs/<id>                  its state; once ended, its result and output links
    GET    /runs/<id>/events           its events as server-sent events, until it ends
    GET    /runs/<id>/outputs/<o>      an output's file (a folder as a zip)
    GET    /runs/<id>/bundle[?with_data=true]    an ended run's evidence, to fix the agent where it is developed
    DELETE /runs/<id>                  cancel it
    GET    /docs, /openapi.json        the description; the run form is generated from the agent's interface

A run's form has a field per param: a file for a path param (several files for a folder), text for a text param, and
`approve` once per side effect the run may perform up front; one it reaches unapproved, or a decision, stops the run
(state input_required, with what it needs) until it is answered. Callers never name a path on the server: every
output param is placed in the run (outputs/<name>), and secrets come from the server's environment. With a token, every request but
/health needs `Authorization: Bearer <token>`; without one, the server listens on loopback only. One JSON line per
request and per run end goes to stdout.
"""
import hmac
import inspect
import json
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Body,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import (
    UploadFile as Upload,  # what arrives: FastAPI's UploadFile is a subclass of it
)

from prometheus import a2a
from prometheus.cli import PrometheusError, run_script
from prometheus.run_release import Agent, Run, public_needs

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def log(**fields):
    print(json.dumps({"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **fields}), flush=True)


class Service:
    """The agent's runs: in memory while this process lives, reopened from their folders after."""

    def __init__(self, agent: Agent, max_runs: int, keep: int | None = None):
        self.agent, self.max_runs, self.keep = agent, max_runs, keep
        self.runs: dict[str, Run] = {}
        self.lock = threading.Lock()

    def get_run(self, run_id: str) -> Run:
        with self.lock:
            run = self.runs.get(run_id)
        if run is None and run_id.replace("-", "").isalnum():
            run = Run.reopen(self.agent, run_id)
        if run is None:
            raise HTTPException(404, f"no run {run_id}")
        return run

    def start(self, params: dict, approve: list[str]) -> Run:
        agent = self.agent
        with self.lock:
            if sum(r.running for r in self.runs.values()) >= self.max_runs:
                raise HTTPException(429, f"{self.max_runs} runs are going already: try again later")
            run = agent.start(params=params, approve=approve, in_run=agent.output_params)
            self.runs[run.id] = run
        log(event="run_start", agent=agent.name, version=agent.version, run=run.id, approve=approve)
        self.watch(run)
        return run

    def answer(self, run: Run, approve: list[str], decisions: dict) -> Run:
        try:
            run.answer(approve, decisions)
        except PrometheusError as exc:
            raise HTTPException(409, str(exc)) from None
        with self.lock:
            self.runs[run.id] = run
        log(event="run_answered", agent=self.agent.name, version=self.agent.version, run=run.id, approve=approve, decided=sorted(decisions))
        self.watch(run)
        return run

    def watch(self, run: Run):
        """Log where the run stops: its end, or a question for the caller."""
        def wait():
            result = run.wait()
            log(event="run_paused" if result.state == "input_required" else "run_end", agent=self.agent.name,
                version=self.agent.version, run=run.id, state=result.state, ok=result.ok, cost_usd=result.cost_usd, error=result.error)
            if self.keep is not None and result.state != "input_required":
                pruned = run_script("run_history.py", "prune", "--agent", str(self.agent.folder), "--keep", str(self.keep))
                if pruned.get("pruned"):
                    log(event="pruned", agent=self.agent.name, runs=pruned["pruned"], freed_mb=pruned.get("freed_mb"))
        threading.Thread(target=wait, daemon=True).start()


def describe(agent: Agent) -> dict:
    return {"name": agent.name, "version": agent.version, "params": agent.params, "outputs": agent.outputs,
            "side_effects": agent.side_effects}


def show_run(agent: Agent, run: Run) -> dict:
    base = f"/runs/{run.id}"
    shown = {"id": run.id, "agent": agent.name, "version": agent.version, "state": "running", "events": f"{base}/events"}
    if run.running:
        return shown
    result = run.wait()
    if result.state == "input_required":
        return {**shown, "state": "input_required", "needs": public_needs(run, result.needs), "answer": f"{base}/answer"}
    return {**shown, "state": result.state, "ok": result.ok, "error": result.error, "cost_usd": result.cost_usd,
            "outputs": {oid: {"url": f"{base}/outputs/{oid}", "ready": bool(path and path.exists())} for oid, path in result.outputs.items()}}


def what_it_needs(preflight: dict) -> dict:
    """A refused run's preflight for the caller: what to send, without the server's paths and programs."""
    return {"params": preflight.get("missing_params") or [], "secrets_on_the_server": preflight.get("missing_secrets") or []}


def run_form(service: Service):
    """The endpoint that starts a run, its form fields made from the agent's interface (so /docs shows them)."""
    agent = service.agent
    folders = agent.folder_params
    fields = {}
    for name, spec in agent.params.items():
        if name in agent.output_params or spec["type"] == "secret":
            continue  # placed in the run; read from the server's environment
        required = spec["required"]
        if spec["type"] == "path":
            many = name in folders
            kind = list[UploadFile] if many else UploadFile
            fields[name] = (Annotated[kind if required else kind | None, File(description="the folder's files" if many else "the file")], required)
        else:
            fields[name] = (Annotated[str if required else str | None, Form()], required)
    side_effects = agent.side_effects

    async def start(wait: bool = False, approve: list[str] | None = None, **values):
        params = {}
        for name, value in values.items():
            if value is None:
                continue
            if isinstance(value, list):
                params[name] = {f.filename or f"file-{i}": await f.read() for i, f in enumerate(value)}
            elif isinstance(value, Upload):
                params[name] = (value.filename or name, await value.read())
            else:
                params[name] = value
        try:  # the compile, and waiting, block: off the event loop, so other requests go on meanwhile
            run = await run_in_threadpool(service.start, params, approve or [])
        except PrometheusError as exc:
            raise HTTPException(400, {"error": str(exc), "needs": what_it_needs(exc.details.get("preflight") or {})}) from None
        if wait:
            await run_in_threadpool(run.wait)
            return JSONResponse(await run_in_threadpool(show_run, agent, run))
        return JSONResponse(await run_in_threadpool(show_run, agent, run), status_code=202, headers={"Location": f"/runs/{run.id}"})

    parameters = [
        inspect.Parameter("wait", inspect.Parameter.KEYWORD_ONLY, default=Query(False, description="answer when the run has ended"),
                          annotation=bool),
        inspect.Parameter("approve", inspect.Parameter.KEYWORD_ONLY, default=Form(None, description=(
            f"a side effect the run may perform, once each: {', '.join(side_effects)}" if side_effects else "none: this agent has no side effects")),
            annotation=list[str] | None),
        *(inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, default=inspect.Parameter.empty if required else None,
                            annotation=kind) for name, (kind, required) in fields.items()),
    ]
    start.__signature__ = inspect.Signature(parameters)
    start.__name__ = f"start_{agent.name.replace('-', '_')}"
    return start


def make_app(agent: Agent, token: str | None = None, max_runs: int = 4, max_upload_mb: int = 100,
             public_url: str = "http://127.0.0.1:8080", keep: int | None = None) -> FastAPI:
    """The app; `public_url` is where callers reach it (the A2A Agent Card and artifact links name it); `keep`: prune
    finished runs beyond the newest N after each run."""
    token = token or None  # an empty token is none: check_host refuses it off loopback
    service = Service(agent, max_runs, keep)

    def check_token(request: Request):
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if token is not None and not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "send Authorization: Bearer <token>")

    app = FastAPI(title=f"{agent.name} {agent.version}", version=agent.version,
                  description=f"The agent {agent.name}, released as {agent.version}, run with no one to ask.")
    guarded = APIRouter(dependencies=[Depends(check_token)])  # everything but /health: a load balancer's probe has no token

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        response = await call_next(request)
        log(method=request.method, path=request.url.path, status=response.status_code)
        return response

    @app.get("/health")
    def health():
        return {"ok": True}

    @guarded.get("/")
    def the_agent():
        return describe(agent)

    guarded.post("/runs", summary=f"Start a run of {agent.name} {agent.version}", status_code=202)(run_form(service))

    @guarded.get("/runs/{run_id}")
    def run_state(run_id: str):
        return show_run(agent, service.get_run(run_id))

    @guarded.get("/runs/{run_id}/events")
    def run_events(run_id: str):
        run = service.get_run(run_id)
        stream = (f"event: {e.get('event', 'message')}\ndata: {json.dumps(e)}\n\n" for e in run.events())
        return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    @guarded.get("/runs/{run_id}/outputs/{output}")
    def run_output(run_id: str, output: str):
        run = service.get_run(run_id)
        if run.running:
            raise HTTPException(409, "the run is still going: its outputs come at the end")
        path = run.planned_outputs.get(output)
        if path is None or not path.exists():
            raise HTTPException(404, f"no output {output} in run {run_id}")
        if not path.resolve().is_relative_to(run.dir.resolve()):
            raise HTTPException(403, "that output is outside the run")
        if path.is_dir():
            tmp = tempfile.mkdtemp()
            archive = shutil.make_archive(str(Path(tmp) / output), "zip", path)
            return FileResponse(archive, filename=f"{output}.zip", background=BackgroundTask(shutil.rmtree, tmp, True))
        return FileResponse(path, filename=path.name)

    @guarded.get("/runs/{run_id}/bundle")
    def run_bundle(run_id: str, with_data: bool = False):
        """An ended run's evidence as one file (run_bundle.py): copy it to where the agent is developed, then
        /agent-improve <file>. Without what the run read unless with_data: the children's sessions and output, its
        inputs, outputs and work (the caller's data)."""
        run = service.get_run(run_id)
        if run.running:
            raise HTTPException(409, "the run is still going: bundle it once it ended")
        tmp = tempfile.mkdtemp()
        made = run_script("run_bundle.py", "make", "--run", str(run.dir), "--out", tmp, *(["--with-data"] if with_data else []))
        if not made.get("ok"):
            shutil.rmtree(tmp, ignore_errors=True)
            raise HTTPException(409, made.get("error") or "the bundle could not be made")
        return FileResponse(made["bundle"], filename=Path(made["bundle"]).name, media_type="application/gzip",
                            background=BackgroundTask(shutil.rmtree, tmp, True))

    @guarded.post("/runs/{run_id}/answer")
    def answer_run(run_id: str, body: Annotated[dict, Body(examples=[{"approve": ["build-workbook"], "decisions": {"pick": "the second"}}])],
                   wait: bool = False):
        """Answer a run that waits for one (state input_required): approve side effects, make decisions."""
        run = service.answer(service.get_run(run_id), list(body.get("approve") or []), dict(body.get("decisions") or {}))
        if wait:
            run.wait()
        return show_run(agent, run)

    @guarded.delete("/runs/{run_id}")
    def cancel_run(run_id: str):
        run = service.get_run(run_id)
        run.cancel()
        return show_run(agent, run)

    app.include_router(guarded)
    a2a.add_routes(app, service, public_url, token)
    app.add_middleware(LimitBody, limit=max_upload_mb * 2**20)
    return app


class LimitBody:
    """Refuse a request body over `limit` bytes (413): by its Content-Length, and by what arrives (a chunked upload
    has none)."""

    def __init__(self, app, limit: int):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        too_big = f"the body is over {self.limit // 2**20} MB"
        length = dict(scope.get("headers") or []).get(b"content-length")
        if length and length.isdigit() and int(length) > self.limit:
            return await JSONResponse({"detail": too_big}, status_code=413)(scope, receive, send)
        seen, refused = 0, False

        async def counted():
            nonlocal seen, refused
            message = await receive()
            seen += len(message.get("body") or b"")
            if seen > self.limit:
                refused = True
                return {"type": "http.disconnect"}  # reads stop here: the app answers some error, replaced below
            return message

        async def answer(message):
            if not refused:
                return await send(message)
            if message["type"] == "http.response.start":  # whatever the app made of a cut body: 413 instead
                body = json.dumps({"detail": too_big}).encode()
                await send({"type": "http.response.start", "status": 413,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                await send({"type": "http.response.body", "body": body})
        await self.app(scope, counted, answer)


def check_host(host: str, token: str | None):
    if not token and host not in LOOPBACK:
        raise PrometheusError(f"serving on {host} needs a token (--token or PROMETHEUS_SERVE_TOKEN): anyone who reaches it could run the agent")
