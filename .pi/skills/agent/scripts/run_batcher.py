#!/usr/bin/env python3
"""The `batcher` command-runner agent (no model) inside a compiled
wave: batching for map stages, and running a stage's program with its gate.

pi-subagents passes the agent instructions plus the task on stdin. The task
contains one line:

  BATCH_TOOL_REQUEST {"op": "split", ...}

The tool reads the last such line, does the work, and prints one JSON object
on stdout (the child's output). Relative paths resolve against the cwd.

split  {"op":"split","source":PATH,"glob":"*.eml"?,"batch":N,"outdir":PATH}
       source is a folder (files matching glob, sorted), a JSON array file,
       a .jsonl file, a .csv file (header repeated in every batch), or any
       other text file (one item per line). Writes <outdir>/<i>.txt (or .csv)
       and prints {"total":N,"batches":[{"index":i,"file":PATH,"count":k}]}.

merge  {"op":"merge","inputs":[PATH,...],"output":PATH,"batches":[PATH,...]?}
       Concatenates by the output's extension: .json (arrays are flattened,
       objects appended), .jsonl / .txt (lines), .csv (header once).
       Missing inputs are an error. With "batches" (the split's batch files),
       every item they list must be named by exactly one record of the result
       (a JSON record holding it as a string value, a line or a CSV row holding
       it; a file's name counts for its path): one missing or named twice fails
       the merge, naming them. Items that are JSON objects have no name and are
       only counted. Prints {"output":PATH,"count":N,"items":{"checked","unnamed"}};
       with "gate" (as in run), the gate's result too, checking the merged output.

run    {"op":"run","command":CMD,"gate":CMD?,"gate_json":BOOL?,"timeout":S?,"secrets":[ENV]?,"env_file":PATH?}
       Runs a stage's program, then its gate, each through the shell (so
       ${SECRET} variables expand, as in gates). Prints {"program": {...},
       "gate": {...}}; the gate's JSON when gate_json. A failing program or
       gate exits 1 with the reason on stderr: pi-subagents fails the child
       with it, and the wave hands the reason to the stage's fix child.
       "timeout" (seconds, default 600) bounds each command: a program that
       hangs fails like one that exits non-zero, instead of hanging the wave.
       "secrets" names the variables the commands need from "env_file" (the
       agent's .env; the environment wins): only they are added, only to
       these commands. Pi's environment holds no secrets, so no model sees one.

a2a    {"op":"a2a","url":URL,"files":{PARAM:PATH},"params":{...},"approve":[STAGE],"token":"${ENV}"?,"out":DIR,
        "timeout":S?,"gate":CMD?,"secrets":[ENV]?,"env_file":PATH?}
       Calls another agent over A2A (1.0, HTTP+JSON): reads its Agent Card at <url>/.well-known/agent-card.json, sends
       one message (a file part per file of each PATH, named by PARAM in its metadata; a data part with params and
       approve), polls the task until it ends, and downloads each artifact into DIR. Prints {"task", "state",
       "artifacts": [PATH]}; then the gate. A task that ends otherwise fails with its message; one that waits for an
       answer fails with what it asks (nobody can answer inside a wave: approve its side effects in the plan).

Can also be run directly: run_batcher.py '<json request>'.
"""
import base64
import csv
import fnmatch
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

MARKER = "BATCH_TOOL_REQUEST "


def fail(message):
    print(json.dumps({"error": message}))
    print(message, file=sys.stderr)  # pi-subagents' error for a command runner that exits non-zero
    sys.exit(1)


def tail(text, limit=2000):
    text = (text or "").strip()
    return text if len(text) <= limit else "…" + text[-limit:]


def read_request(argv):
    if len(argv) > 1:
        return json.loads(argv[1])
    text = sys.stdin.read()
    lines = [l for l in text.splitlines() if l.strip().startswith(MARKER)]
    if not lines:
        fail(f"no {MARKER.strip()} line on stdin")
    return json.loads(lines[-1].strip()[len(MARKER):])


def items_from(source, glob):
    source = Path(source)
    if source.is_dir():
        names = sorted(p.name for p in source.iterdir() if p.is_file() and fnmatch.fnmatch(p.name, glob or "*"))
        return [str((source / n).resolve()) for n in names], None
    if not source.is_file():
        fail(f"source not found: {source}")
    ext = source.suffix.lower()
    text = source.read_text(encoding="utf-8")
    if ext == ".json":
        data = json.loads(text) if text.strip() else []
        if not isinstance(data, list):
            fail(f"{source} is not a JSON array")
        return [json.dumps(x, ensure_ascii=False) for x in data], None
    if ext == ".csv":
        rows = list(csv.reader(io.StringIO(text)))
        if not rows:
            return [], None
        return rows[1:], rows[0]
    return [l for l in text.splitlines() if l.strip()], None


def split(req):
    batch = req.get("batch")
    if not isinstance(batch, int) or batch <= 0:
        fail("batch must be a positive integer")
    source, outdir = req.get("source"), req.get("outdir")
    if not source or not outdir:
        fail("split needs source and outdir")
    items, header = items_from(source, req.get("glob"))
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    batches = []
    for index, start in enumerate(range(0, len(items), batch)):
        chunk = items[start:start + batch]
        if header is not None:
            path = (outdir / f"{index}.csv").resolve()
            with path.open("w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(header)
                w.writerows(chunk)
        else:
            path = (outdir / f"{index}.txt").resolve()
            path.write_text("\n".join(chunk) + "\n", encoding="utf-8")
        batches.append({"index": index, "file": str(path), "count": len(chunk)})
    return {"total": len(items), "batches": batches}


def batch_items(batches):
    """The names of the items the split wrote into `batches`, and how many items have none (JSON objects)."""
    names, unnamed = [], 0
    for path in map(Path, batches):
        if path.suffix == ".csv":
            with path.open(encoding="utf-8", newline="") as fh:
                names += [row[0] for row in list(csv.reader(fh))[1:] if row]  # a row is named by its first cell
            continue
        for line in (l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()):
            try:
                value = json.loads(line)
            except ValueError:
                value = line  # a path or a line of text
            if isinstance(value, (dict, list)):
                unnamed += 1
            else:
                names.append(str(value))
    return names, unnamed


def strings_in(value):
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings_in(v)]
    return [str(value)] if value is not None else []


def check_items(names, records, kind):
    """Fail unless each name is named by exactly one record: a JSON record (any string value), a line, a CSV row."""
    def forms(name):  # a batch file lists files by their path; a result may name them by file name
        return {name, Path(name).name} if "/" in name else {name}

    def names_it(record, name):
        if kind == "lines":
            return any(re.search(rf"(?<![\w.-]){re.escape(f)}(?![\w-])", record) for f in forms(name))
        base = Path(name).name
        return any(v == name or ("/" in name and Path(v).name == base) for v in record)

    found = {name: sum(names_it(r, name) for r in records) for name in names}
    missing = [n for n, k in found.items() if k == 0]
    twice = [n for n, k in found.items() if k > 1]
    if missing or twice:
        show = lambda xs: ", ".join(Path(x).name if "/" in x else x for x in xs[:10]) + (f" and {len(xs) - 10} more" if len(xs) > 10 else "")
        fail("the merged result does not name every item of the split exactly once"
             + (f"; missing: {show(missing)}" if missing else "") + (f"; named more than once: {show(twice)}" if twice else ""))


def merge(req):
    inputs, output = req.get("inputs"), req.get("output")
    if not isinstance(inputs, list) or not output:
        fail("merge needs inputs (list) and output")
    missing = [p for p in inputs if not Path(p).is_file()]
    if missing:
        fail(f"missing batch outputs: {missing}")
    output = Path(output)
    ext = output.suffix.lower()
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    if ext == ".json":
        merged = []
        for p in inputs:
            text = Path(p).read_text(encoding="utf-8")
            data = json.loads(text) if text.strip() else []
            merged.extend(data if isinstance(data, list) else [data])
        output.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8")
        count, records, kind = len(merged), [strings_in(r) for r in merged], "values"
    elif ext == ".csv":
        header, rows = None, []
        for p in inputs:
            with Path(p).open(encoding="utf-8", newline="") as fh:
                data = list(csv.reader(fh))
            if not data:
                continue
            if header is None:
                header = data[0]
            elif data[0] != header:
                fail(f"{p} has a different CSV header")
            rows.extend(data[1:])
        with output.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            if header is not None:
                w.writerow(header)
            w.writerows(rows)
        count, records, kind = len(rows), rows, "values"
    else:
        lines = []
        for p in inputs:
            lines.extend(l for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip())
        output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        count, records, kind = len(lines), lines, "lines"
    result = {"output": str(output.resolve()), "count": count}
    if batches := req.get("batches"):
        names, unnamed = batch_items(batches)
        check_items(names, records, kind)
        result["items"] = {"checked": len(names), "unnamed": unnamed}
    return result


def dotenv(path):
    """KEY=value lines of a .env file (python-dotenv's common forms: comments, `export `, quotes)."""
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines() if Path(path).is_file() else []:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == "'":
            value = value[1:-1]
        elif len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1].encode().decode("unicode_escape")
        else:
            value = value.split(" #", 1)[0].strip()
        values[key.strip()] = value
    return values


ENV = None  # the environment the commands run with (main sets it: the request's secrets added)


def shell(command, timeout, failed):
    """Run `command` through the shell; on a non-zero exit or a timeout, fail with `failed` and the reason."""
    try:
        p = subprocess.run(command, shell=True, capture_output=True, text=True, check=False, timeout=timeout, env=ENV)
    except subprocess.TimeoutExpired:
        fail(f"{failed}: no result within {timeout} s (limits.command_timeout_s)")
    if p.returncode != 0:
        fail(f"{failed} (exit code {p.returncode}): {tail(p.stderr or p.stdout)}")
    return p


def gate(req, timeout):
    """The gate's result ({exit: 0} or its JSON), or None without a gate."""
    if not req.get("gate"):
        return None
    g = shell(req["gate"], timeout, "the gate failed")
    if not req.get("gate_json"):
        return {"exit": 0}
    try:
        return json.loads(g.stdout)
    except ValueError:
        fail(f"the gate did not print JSON: {tail(g.stdout)}")


def run(req):
    if not req.get("command"):
        fail("run needs command")
    timeout = req.get("timeout") or 600
    p = shell(req["command"], timeout, "the program failed")
    result = {"program": {"exit": 0, "stdout": tail(p.stdout)}}
    if (g := gate(req, timeout)) is not None:
        result["gate"] = g
    return result


A2A_END = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED",
           "TASK_STATE_INPUT_REQUIRED", "TASK_STATE_AUTH_REQUIRED"}


def http(method, url, token=None, body=None, timeout=60):
    """(status, bytes) of an HTTP request; A2A errors fail with their message."""
    headers = {"A2A-Version": "1.0", "Accept": "application/a2a+json, application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data, headers["Content-Type"] = json.dumps(body).encode(), "application/a2a+json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method=method), timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        text = exc.read().decode(errors="replace")
        try:
            message = json.loads(text)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = tail(text, 500)
        fail(f"{method} {url}: HTTP {exc.code}: {message}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        fail(f"{method} {url}: {exc}")


def origin(url):
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, parts.netloc.lower()


def a2a(req):
    url = (req.get("url") or "").rstrip("/")
    if not url.startswith(("http://", "https://")):
        fail(f"a2a needs the agent's http(s) address, not {url!r}")
    token = None
    if req.get("token"):  # "${ENV}": from the environment, with the secrets this request names (main adds them to ENV)
        token = req["token"]
        for name, value in (ENV or os.environ).items():
            token = token.replace("${" + name + "}", value)
        if "${" in token:
            fail(f"a2a: the token's variable {req['token']} is not set (the agent's .env or the environment)")
    card_url = url if url.endswith("/agent-card.json") else f"{url}/.well-known/agent-card.json"
    card = json.loads(http("GET", card_url, token)[1])
    endpoint = next((i["url"].rstrip("/") for i in card.get("supportedInterfaces") or [] if i.get("protocolBinding") == "HTTP+JSON"), None)
    if not endpoint:
        fail(f"the agent at {url} offers no HTTP+JSON interface (its card lists {[i.get('protocolBinding') for i in card.get('supportedInterfaces') or []]})")

    def ours(link, what):
        """A URL the agent gave, followed (with the token) only on its own address: never elsewhere."""
        if origin(link) != origin(url):
            fail(f"the agent at {url} gave {what} on another address ({link}): not followed, so the token and the "
                 "files go nowhere else (its public url must be the address the plan names)")
        return link
    endpoint = ours(endpoint, "its endpoint")
    parts = []
    for param, path in (req.get("files") or {}).items():
        source = Path(path)
        files = sorted(p for p in source.rglob("*") if p.is_file()) if source.is_dir() else [source]
        if not files or not all(f.is_file() for f in files):
            fail(f"a2a: nothing to send as {param}: {path} is missing or empty")
        parts += [{"raw": base64.b64encode(f.read_bytes()).decode(), "filename": f.name, "metadata": {"param": param}} for f in files]
    parts.append({"data": {"params": req.get("params") or {}, "approve": req.get("approve") or []}})
    message = {"messageId": str(uuid.uuid4()), "role": "ROLE_USER", "parts": parts}
    timeout = req.get("timeout") or 600
    task = json.loads(http("POST", f"{endpoint}/message:send", token,
                           {"message": message, "configuration": {"returnImmediately": True}})[1]).get("task") or {}
    deadline = time.monotonic() + timeout
    while (task.get("status") or {}).get("state") not in A2A_END:
        if time.monotonic() > deadline:
            fail(f"the agent at {url} did not finish task {task.get('id')} within {timeout} s (limits.command_timeout_s)")
        time.sleep(2)
        task = json.loads(http("GET", f"{endpoint}/tasks/{task['id']}", token)[1])
    state = task["status"]["state"]
    said = " ".join(p.get("text", "") for p in ((task["status"].get("message") or {}).get("parts") or []) if p.get("text"))
    if state == "TASK_STATE_INPUT_REQUIRED":
        fail(f"the agent at {url} asks before it goes on (task {task['id']}): {said or 'no question given'}; nobody can answer "
             "inside a wave: approve its side effects in the stage's a2a.approve")
    if state != "TASK_STATE_COMPLETED":
        fail(f"the agent at {url} ended task {task['id']} as {state.removeprefix('TASK_STATE_').lower()}: {said or 'no reason given'}")
    out = Path(req.get("out") or ".")
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for artifact in task.get("artifacts") or []:
        for n, part in enumerate(artifact.get("parts") or []):
            fallback = f"{Path(str(artifact.get('artifactId') or 'artifact')).name or 'artifact'}-{n}"
            name = Path(str(part.get("filename") or "")).name
            name = fallback if name in ("", ".", "..") else name
            if "url" in part:
                data = http("GET", ours(part["url"], "an artifact"), token, timeout=timeout)[1]
            elif "raw" in part:
                data = base64.b64decode(part["raw"])
            elif "data" in part:
                data, name = json.dumps(part["data"], indent=1).encode(), f"{Path(name).stem}.json"
            else:
                data, name = (part.get("text") or "").encode(), f"{Path(name).stem}.txt"
            if str(out / name) in written:  # two parts named alike: neither lost
                name = f"{Path(name).stem}-{n}{Path(name).suffix}"
            (out / name).write_bytes(data)
            written.append(str(out / name))
    return {"task": task["id"], "state": state, "artifacts": written}


def main(argv):
    try:
        req = read_request(argv)
    except ValueError as exc:
        fail(f"bad request JSON: {exc}")
    global ENV
    if req.get("secrets"):
        found = dotenv(req.get("env_file") or "")
        ENV = {**os.environ, **{k: found[k] for k in req["secrets"] if k in found and k not in os.environ}}
    op = req.get("op")
    if op == "split":
        result = split(req)
    elif op == "merge":
        result = merge(req)
        if (g := gate(req, req.get("timeout") or 600)) is not None:
            result["gate"] = g
    elif op == "run":
        result = run(req)
    elif op == "a2a":
        result = a2a(req)
        if (g := gate(req, req.get("timeout") or 600)) is not None:
            result["gate"] = g
    else:
        fail(f"unknown op {op!r}")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
