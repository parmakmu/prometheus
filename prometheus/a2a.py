"""A2A (Agent2Agent, protocol 1.0, HTTP+JSON binding) for `prometheus serve`: the same runs as /runs, as A2A tasks.

    GET  /.well-known/agent-card.json     the Agent Card (no token: discovery), one skill: the agent
    POST /message:send                    a message → a task (blocks until it ends, unless configuration.returnImmediately)
    POST /message:stream                  the same, as server-sent events (StreamResponse objects)
    GET  /tasks/{id}, GET /tasks          a task; the tasks (contextId, status, pageSize, pageToken)
    POST /tasks/{id}:cancel               cancel it
    POST /tasks/{id}:subscribe            its updates as server-sent events, until it ends

What a message carries: a file part (`raw`, base64, with `filename`) per file of a path param, named in the part's
`metadata.param` (or not, when the agent has one path param; several files for one param make a folder), and one
data part {"params": {...}, "approve": [...]} with the text params and the side effects the run may perform. Text parts
are kept as the request's words and not read. URL parts are refused (the server fetches nothing a caller names).
Outputs come back as artifacts whose part is a `url` to download it (with the same token). Errors are google.rpc.Status
objects with an ErrorInfo (domain a2a-protocol.org). Push notifications and the extended card are not offered.
"""
import base64
import binascii
import datetime
import hmac
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from prometheus import cli
from prometheus.cli import PrometheusError
from prometheus.frozen import Run, public_needs

PROTOCOL = "1.0"
CONTENT_TYPE = "application/a2a+json"
STATES = {"complete": "TASK_STATE_COMPLETED", "stopped": "TASK_STATE_CANCELED", "input_required": "TASK_STATE_INPUT_REQUIRED"}
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED"}
CONTEXT_FILE = "a2a.json"  # in the run folder: the task's contextId, so a restarted server answers the same
# the card's machine-readable interface (the release's, exactly): a new URI for a breaking change to its shape, as A2A asks
INTERFACE_EXTENSION = "urn:prometheus:a2a:interface:v1"


def interface_extension(agent) -> dict:
    """The release's interface as an A2A extension: params (type, required; files, folders, or placed in the run),
    outputs, side effects. Clients that don't know it ignore it (`required: false`)."""
    takes = {}
    for name, spec in agent.params.items():
        if spec["type"] == "secret":
            how = "server_environment"
        elif name in agent.output_params:
            how = "placed_in_run"
        elif spec["type"] == "path":
            how = "files" if name in agent.folder_params else "file"
        else:
            how = "data"
        takes[name] = {**spec, "sent_as": how}
    return {"uri": INTERFACE_EXTENSION, "required": False,
            "description": "The release's exact interface: what each param takes and how it is sent, its outputs, and the side effects a run asks to approve.",
            "params": {"agent": agent.name, "version": agent.version, "params": takes, "outputs": agent.outputs,
                       "side_effects": agent.side_effects}}


class A2AError(Exception):
    """An A2A error: its HTTP status, google.rpc code, ErrorInfo reason and message."""

    def __init__(self, http: int, code: str, reason: str, message: str, details: list | None = None):
        super().__init__(message)
        self.http, self.code, self.reason, self.details = http, code, reason, details or []

    def body(self) -> dict:
        info = {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": self.reason, "domain": "a2a-protocol.org"}
        return {"error": {"code": self.http, "status": self.code, "message": str(self), "details": [info, *self.details]}}


def bad_request(message: str, reason: str = "INVALID_PARAMS") -> A2AError:
    return A2AError(400, "INVALID_ARGUMENT", reason, message)


def now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def answer(body: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(body, status_code=status, media_type=CONTENT_TYPE)


def agent_card(agent, public_url: str, token: bool) -> dict:
    """The Agent Card: the agent as one skill, its description saying what a message must carry."""
    plan = cli.import_script("utils").read_plan(agent.folder, {}) or {}
    takes = []
    for name, spec in agent.params.items():
        if name in agent.output_params or spec["type"] == "secret":
            continue
        how = ("files (a file part each, metadata.param = this name)" if name in agent.folder_params else
               "a file (a file part, metadata.param = this name)") if spec["type"] == "path" else "text, in the data part's params"
        takes.append(f"{name} ({'required' if spec['required'] else 'optional'}): {how}")
    approve = (f" The data part's approve lists the side effects the run may perform: {', '.join(agent.side_effects)}; one not "
               "approved refuses the run.") if agent.side_effects else ""
    skill = {"id": agent.name, "name": agent.name, "tags": ["prometheus", "released"],
             "description": f"{(plan.get('goal') or agent.name).strip()} Takes: {'; '.join(takes) or 'nothing'}.{approve} "
                            f"Returns its outputs ({', '.join(agent.outputs) or 'none'}) as artifacts with a download url.",
             "inputModes": ["application/octet-stream", "application/json"], "outputModes": ["application/octet-stream"]}
    card = {"name": agent.name, "description": (plan.get("goal") or agent.name).strip(), "version": agent.version,
            "supportedInterfaces": [{"url": public_url, "protocolBinding": "HTTP+JSON", "protocolVersion": PROTOCOL}],
            "capabilities": {"streaming": True, "pushNotifications": False, "extendedAgentCard": False,
                             "extensions": [interface_extension(agent)]},
            "defaultInputModes": ["application/octet-stream", "application/json"], "defaultOutputModes": ["application/octet-stream"],
            "skills": [skill]}
    if token:
        card["securitySchemes"] = {"bearer": {"httpAuthSecurityScheme": {"scheme": "Bearer"}}}
        card["securityRequirements"] = [{"schemes": {"bearer": {"list": []}}}]
    return card


def read_answer(message: dict) -> tuple[list[str], dict]:
    """(approve, decisions) from an answering message's data parts."""
    approve, decisions = [], {}
    for part in message.get("parts") or []:
        if isinstance(part, dict) and isinstance(part.get("data"), dict):
            approve += [str(a) for a in part["data"].get("approve") or []]
            decisions.update(part["data"].get("decisions") or {})
    return approve, decisions


def read_message(agent, message: dict) -> tuple[dict, list[str]]:
    """(params, approve) from a message's parts."""
    if not isinstance(message, dict) or not isinstance(message.get("parts"), list) or not message.get("messageId"):
        raise bad_request("a message needs messageId and parts")
    path_params = [n for n, s in agent.params.items() if s["type"] == "path" and n not in agent.output_params]
    files: dict[str, dict] = {}
    params, approve = {}, []
    for part in message["parts"]:
        if not isinstance(part, dict):
            raise bad_request("each part is an object")
        if "url" in part:
            raise A2AError(400, "INVALID_ARGUMENT", "CONTENT_TYPE_NOT_SUPPORTED", "url parts are not accepted: send the file's bytes as raw")
        if "raw" in part:
            name = (part.get("metadata") or {}).get("param") or (path_params[0] if len(path_params) == 1 else None)
            if name not in path_params:
                raise bad_request(f"a file part needs metadata.param, one of: {', '.join(path_params) or 'none (this agent takes no files)'}")
            try:
                data = base64.b64decode(part["raw"], validate=True)
            except (binascii.Error, TypeError):
                raise bad_request("raw is not base64") from None
            files.setdefault(name, {})[Path(part.get("filename") or f"{name}-{len(files.get(name, {}))}").name] = data
        elif "data" in part:
            data = part["data"] if isinstance(part["data"], dict) else {}
            params.update(data.get("params") or {})
            approve += [str(a) for a in data.get("approve") or []]
    for name, found in files.items():
        if name in agent.folder_params:
            params[name] = found
        elif len(found) == 1:
            params[name] = next(iter(found.items()))
        else:
            raise bad_request(f"{name} is one file, and {len(found)} came")
    return params, approve


def task_state(run: Run) -> tuple[str, str | None, dict | None]:
    """The run's A2A state, why it failed or was canceled, and what it needs when it waits for an answer."""
    if run.running:
        launched = any(e.get("event") == "launched" for e in run.read_events())
        return ("TASK_STATE_WORKING" if launched else "TASK_STATE_SUBMITTED"), None, None
    result = run.wait()
    return STATES.get(result.state, "TASK_STATE_FAILED"), result.error, result.needs


def asking(needs: dict) -> str:
    """What a paused run asks, in words, and how to answer it."""
    lines = [f"approve {a['stage']} ({a.get('title') or ''}): it writes {', '.join(a.get('writes') or []) or 'nothing listed'}"
             for a in needs.get("approve") or []]
    lines += [f"decide {d['stage']} ({d.get('title') or ''})" for d in needs.get("decide") or []]
    return ("This task waits for an answer: " + "; ".join(lines) + ". Answer with a message on this task (taskId) whose "
            'data part is {"approve": [stages], "decisions": {stage: answer}}; cancel the task to decline.')


def task_of(run: Run, base_url: str, history: dict | None = None) -> dict:
    state, why, needs = task_state(run)
    context = read_context(run)
    status = {"state": state, "timestamp": now()}
    if needs:
        public = public_needs(run, needs)
        status["message"] = {"messageId": f"{run.id}-needs", "role": "ROLE_AGENT", "taskId": run.id, "contextId": context,
                             "parts": [{"text": asking(public)}, {"data": {"needs": public}}]}
    elif why:
        status["message"] = {"messageId": f"{run.id}-status", "role": "ROLE_AGENT", "parts": [{"text": why}], "taskId": run.id, "contextId": context}
    task = {"id": run.id, "contextId": context, "status": status, "metadata": {"agent": run.agent.name, "version": run.agent.version}}
    if state == "TASK_STATE_COMPLETED":
        task["artifacts"] = [{"artifactId": oid, "name": oid, "parts": [{"url": f"{base_url}/runs/{run.id}/outputs/{oid}",
                                                                          "filename": path.name + (".zip" if path.is_dir() else "")}]}
                             for oid, path in run.planned_outputs.items() if path and path.exists()]
    if history:
        task["history"] = [history]
    return task


def read_context(run: Run) -> str:
    try:
        return json.loads((run.dir / CONTEXT_FILE).read_text(encoding="utf-8"))["contextId"]
    except (OSError, ValueError, KeyError):
        return run.id


def stream(run: Run, base_url: str, first: dict | None = None, seen: int = 0):
    """StreamResponse objects as server-sent events: the task, then status updates as stages move, until it stops (it
    ends, or waits for an answer); `seen`: events from before (an answered task streams from its answer on)."""
    context = read_context(run)
    yield f"data: {json.dumps({'task': first or task_of(run, base_url)})}\n\n"
    for n, event in enumerate(run.events()):
        if n < seen:
            continue
        if event.get("event") == "child":
            text = f"{event.get('key')}: {event.get('state')}"
            update = {"taskId": run.id, "contextId": context, "status": {"state": "TASK_STATE_WORKING", "timestamp": now(),
                      "message": {"messageId": f"{run.id}-{uuid.uuid4().hex[:8]}", "role": "ROLE_AGENT", "parts": [{"text": text}]}}}
            yield f"data: {json.dumps({'statusUpdate': update})}\n\n"
    final = task_of(run, base_url)
    for artifact in final.get("artifacts") or []:
        yield f"data: {json.dumps({'artifactUpdate': {'taskId': run.id, 'contextId': context, 'artifact': artifact, 'lastChunk': True}})}\n\n"
    yield f"data: {json.dumps({'statusUpdate': {'taskId': run.id, 'contextId': context, 'status': final['status']}})}\n\n"


def add_routes(app: FastAPI, service, public_url: str, token: str | None):
    """The A2A routes, beside serve's own (they share the runs, the limit and the download route)."""
    base = public_url.rstrip("/")

    @app.exception_handler(A2AError)
    async def a2a_error(_request: Request, exc: A2AError):
        return answer(exc.body(), exc.http)

    def check(request: Request):
        version = request.headers.get("a2a-version") or request.query_params.get("A2A-Version")
        if version and version.split(".")[0] != PROTOCOL.split(".")[0]:
            raise A2AError(400, "INVALID_ARGUMENT", "VERSION_NOT_SUPPORTED", f"A2A version {version} is not supported: this agent speaks {PROTOCOL}")
        given = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if token is not None and not hmac.compare_digest(given.encode(), token.encode()):
            raise A2AError(401, "UNAUTHENTICATED", "UNAUTHENTICATED", "send Authorization: Bearer <token>")

    router = APIRouter(dependencies=[Depends(check)], tags=["A2A"])

    @app.get("/.well-known/agent-card.json", tags=["A2A"])
    def card():
        return answer(agent_card(service.agent, base, token is not None))

    def start(body: dict) -> tuple[Run, dict, int]:
        """A new task from the message, or the task it answers (a message naming a waiting task's id); with the number
        of the task's events from before."""
        message = body.get("message") if isinstance(body, dict) else None
        if isinstance(message, dict) and message.get("taskId"):
            run = get_task(message["taskId"])
            seen = len(run.read_events())
            approve, decisions = read_answer(message)
            try:
                service.answer(run, approve, decisions)
            except HTTPException as exc:
                raise A2AError(400, "FAILED_PRECONDITION", "TASK_NOT_WAITING", str(exc.detail)) from None
            return run, message, seen
        params, approve = read_message(service.agent, message)
        try:
            run = service.start(params, approve)
        except PrometheusError as exc:
            raise bad_request(str(exc)) from None
        except HTTPException as exc:  # the limit on runs at once
            raise A2AError(exc.status_code, "RESOURCE_EXHAUSTED", "TOO_MANY_RUNS", str(exc.detail)) from None
        (run.dir / CONTEXT_FILE).write_text(json.dumps({"contextId": message.get("contextId") or str(uuid.uuid4())}), encoding="utf-8")
        return run, message, 0

    @router.post("/message:send")
    async def send(request: Request):
        body = await read_body(request)
        run, message, _ = await run_in_threadpool(start, body)  # these block: off the event loop
        if not (body.get("configuration") or {}).get("returnImmediately"):  # blocking is the spec's default
            await run_in_threadpool(run.wait)
        return answer({"task": await run_in_threadpool(task_of, run, base, message)})

    @router.post("/message:stream")
    async def send_streaming(request: Request):
        run, message, seen = await run_in_threadpool(start, await read_body(request))
        first = await run_in_threadpool(task_of, run, base, message)
        return StreamingResponse(stream(run, base, first, seen), media_type="text/event-stream")

    def get_task(task_id: str) -> Run:
        try:
            return service.get_run(task_id)
        except HTTPException:
            raise A2AError(404, "NOT_FOUND", "TASK_NOT_FOUND", f"no task {task_id}") from None

    @router.get("/tasks/{task_id}")
    def one_task(task_id: str):
        return answer(task_of(get_task(task_id), base))

    @router.get("/tasks")
    def list_tasks(contextId: str | None = None, status: str | None = None, pageSize: int = 50, pageToken: str | None = None):
        ids = sorted((p.parent.name for p in (service.agent.folder / "runs").glob(f"*/{CONTEXT_FILE}")), reverse=True)
        tasks = [task_of(service.get_run(i), base) for i in ids]
        tasks = [t for t in tasks if (not contextId or t["contextId"] == contextId) and (not status or t["status"]["state"] == status)]
        start_at = int(pageToken or 0)
        page = tasks[start_at:start_at + max(1, min(pageSize, 100))]
        more = start_at + len(page) < len(tasks)
        return answer({"tasks": page, "nextPageToken": str(start_at + len(page)) if more else "", "pageSize": len(page), "totalSize": len(tasks)})

    @router.post("/tasks/{task_id}:cancel")
    def cancel(task_id: str):
        run = get_task(task_id)
        if task_state(run)[0] in TERMINAL:
            raise A2AError(400, "FAILED_PRECONDITION", "TASK_NOT_CANCELABLE", f"task {task_id} has ended")
        run.cancel()
        return answer(task_of(run, base))

    @router.post("/tasks/{task_id}:subscribe")
    def subscribe(task_id: str):
        run = get_task(task_id)
        if task_state(run)[0] in TERMINAL:
            raise A2AError(400, "FAILED_PRECONDITION", "UNSUPPORTED_OPERATION", f"task {task_id} has ended: get it instead")
        return StreamingResponse(stream(run, base), media_type="text/event-stream")

    @router.api_route("/tasks/{task_id}/pushNotificationConfigs{rest:path}", methods=["GET", "POST", "DELETE"])
    def push(task_id: str, rest: str = ""):
        raise A2AError(400, "FAILED_PRECONDITION", "PUSH_NOTIFICATION_NOT_SUPPORTED", "push notifications are not offered: stream or poll")

    @router.get("/extendedAgentCard")
    def extended():
        raise A2AError(400, "FAILED_PRECONDITION", "EXTENDED_AGENT_CARD_NOT_CONFIGURED", "there is no extended card: the public one is all")

    app.include_router(router)


async def read_body(request: Request) -> dict:
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        raise bad_request("the body is not JSON") from None
    if not isinstance(body, dict):
        raise bad_request("the body is a JSON object")
    return body
