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
       With "items_file":PATH and "stores":[DIR,...] (per-item reuse): writes every item, in order, to
       items_file ({named, items: [{name, key, known}]}; key: a hash of a file's name and contents, else of
       the item), and leaves out of the batches each item a store has <key>.json for (its result from an
       earlier run); prints "known": K too. Only when every item has a name (not JSON objects).

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
       With "items_file" (the split's) and "store":DIR: the stored results of the items the split left out
       come back in (an earlier run's path for an item renamed to this run's), the check covers every item,
       records go in the items' order (one naming no item last), and once the gate passed each record that
       names exactly one item is written to DIR/<key>.json, the reused ones too, so this run's store is whole;
       "items" adds "reused" and "stored".

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
import hashlib
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


def name_of(item):
    """The name a result gives an item, or None (a JSON object or list has none): a CSV row by its first cell, a line
    of JSON by its value, anything else (a path, a line of text) as written."""
    if isinstance(item, list):
        return item[0] if item else None
    try:
        value = json.loads(item)
    except ValueError:
        return item
    return None if isinstance(value, (dict, list)) else str(value)


def key_of(item, folder):
    """What an item's stored result is found by: a hash of a file's name and contents, else of the item itself."""
    if folder:
        path = Path(item)
        return hashlib.sha256(b"file\0" + path.name.encode() + b"\0" + path.read_bytes()).hexdigest()
    return hashlib.sha256(b"item\0" + json.dumps(item, ensure_ascii=False).encode()).hexdigest()


def list_items(items, folder, stores):
    """Each item's name, key and stored result (the first of `stores` that has its key), or None. Only items that all
    have a name are looked up: a result is matched to its item by the name it gives it."""
    listed = [{"name": name_of(it), "key": key_of(it, folder), "known": None} for it in items]
    named = all(it["name"] is not None for it in listed)
    for it in listed if named else []:
        it["known"] = next((str(p) for s in stores if (p := Path(s) / f"{it['key']}.json").is_file()), None)
    return {"named": named, "items": listed}


def split(req):
    batch = req.get("batch")
    if not isinstance(batch, int) or batch <= 0:
        fail("batch must be a positive integer")
    source, outdir = req.get("source"), req.get("outdir")
    if not source or not outdir:
        fail("split needs source and outdir")
    items, header = items_from(source, req.get("glob"))
    known = 0
    if items_file := req.get("items_file"):
        # every item, in order, for the merge: those an earlier run's merge stored a result for are left out of the batches
        listed = list_items(items, Path(source).is_dir(), req.get("stores") or [])
        write_json(items_file, listed)
        items = [it for it, entry in zip(items, listed["items"]) if not entry["known"]]
        known = len(listed["items"]) - len(items)
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
    return {"total": len(items) + known, "batches": batches, **({"known": known} if items_file else {})}


def batch_items(batches):
    """The names of the items the split wrote into `batches`, and how many items have none (JSON objects)."""
    names, unnamed = [], 0
    for path in map(Path, batches):
        if path.suffix == ".csv":
            with path.open(encoding="utf-8", newline="") as fh:
                rows = [row for row in list(csv.reader(fh))[1:] if row]
        else:
            rows = [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
        for name in map(name_of, rows):
            if name is None:
                unnamed += 1
            else:
                names.append(name)
    return names, unnamed


def strings_in(value):
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings_in(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings_in(v)]
    return [str(value)] if value is not None else []


def rename_in(value, old, new):
    """`value` with the item's old name (an earlier run's path) written as its name in this run."""
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, dict):
        return {k: rename_in(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [rename_in(v, old, new) for v in value]
    return value


def attribute_records(names, records, kind):
    """For each record, the indexes of the items it names. Fails unless each item is named by exactly one record: a
    JSON record (any string value), a line, a CSV row."""
    def forms(name):  # a batch file lists files by their path; a result may name them by file name
        return {name, Path(name).name} if "/" in name else {name}

    def names_it(record, name):
        if kind == "lines":
            return any(re.search(rf"(?<![\w.-]){re.escape(f)}(?![\w-])", record) for f in forms(name))
        base = Path(name).name
        return any(v == name or ("/" in name and Path(v).name == base) for v in record)

    named = [[i for i, name in enumerate(names) if names_it(r, name)] for r in records]
    found = [sum(i in n for n in named) for i in range(len(names))]
    missing = [names[i] for i, k in enumerate(found) if k == 0]
    twice = [names[i] for i, k in enumerate(found) if k > 1]
    if missing or twice:
        show = lambda xs: ", ".join(Path(x).name if "/" in x else x for x in xs[:10]) + (f" and {len(xs) - 10} more" if len(xs) > 10 else "")
        fail("the merged result does not name every item of the split exactly once"
             + (f"; missing: {show(missing)}" if missing else "") + (f"; named more than once: {show(twice)}" if twice else ""))
    return named


def read_records(inputs, ext):
    """The batch outputs' records, in order (a JSON value, a CSV row or a line), and the CSV header."""
    header, records = None, []
    for p in inputs:
        text = Path(p).read_text(encoding="utf-8")
        if ext == ".json":
            data = json.loads(text) if text.strip() else []
            records.extend(data if isinstance(data, list) else [data])
        elif ext == ".csv":
            rows = list(csv.reader(io.StringIO(text)))
            if rows:
                header = same_header(header, rows[0], p)
                records.extend(rows[1:])
        else:
            records.extend(l for l in text.splitlines() if l.strip())
    return records, header


def same_header(header, other, where):
    if header is not None and other != header:
        fail(f"{where} has a different CSV header")
    return other


def write_records(output, ext, records, header):
    if ext == ".json":
        output.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    elif ext == ".csv":
        with output.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            if header is not None:
                w.writerow(header)
            w.writerows(records)
    else:
        output.write_text("\n".join(records) + ("\n" if records else ""), encoding="utf-8")


def merge(req):
    """The merged output, and with an `items_file` (the split's list of every item) the records to store: each item's
    own one, so a later run with the same items leaves it out of its batches. Records an earlier run stored come back
    in, and every record goes at its item's place: the items' order, whatever the batches were."""
    inputs, output = req.get("inputs"), req.get("output")
    if not isinstance(inputs, list) or not output:
        fail("merge needs inputs (list) and output")
    missing = [p for p in inputs if not Path(p).is_file()]
    if missing:
        fail(f"missing batch outputs: {missing}")
    output = Path(output)
    ext = output.suffix.lower()
    output.parent.mkdir(parents=True, exist_ok=True)
    records, header = read_records(inputs, ext)
    listed = read_json_file(req["items_file"]) if req.get("items_file") else None
    per_item = bool(listed and listed.get("named"))
    if per_item:
        for item in (it for it in listed["items"] if it["known"]):
            stored = read_json_file(item["known"])
            if ext == ".csv":
                header = same_header(header, stored.get("header"), item["known"])
            records.append(rename_in(stored["record"], stored["item"], item["name"]))
    kind = "values" if ext in (".json", ".csv") else "lines"
    searchable = [strings_in(r) for r in records] if ext == ".json" else records
    result, entries = {"output": str(output.resolve()), "count": len(records)}, []
    if per_item:
        items = listed["items"]
        named = attribute_records([it["name"] for it in items], searchable, kind)
        # one record per item is that item's to store; one naming several, or none, stays out of the store
        entries = [{"key": items[n[0]]["key"], "item": items[n[0]]["name"], "record": r, **({"header": header} if ext == ".csv" else {})}
                   for r, n in zip(records, named) if len(n) == 1]
        place = [n[0] if n else len(items) for n in named]  # a record naming no item goes last
        records = [r for _, _, r in sorted(zip(place, range(len(records)), records))]
        result["items"] = {"checked": len(items), "unnamed": 0, "reused": sum(1 for it in items if it["known"]),
                           "stored": len(entries)}
    elif batches := req.get("batches"):
        names, unnamed = batch_items(batches)
        attribute_records(names, searchable, kind)
        result["items"] = {"checked": len(names), "unnamed": unnamed}
    write_records(output, ext, records, header)
    return result, entries


def store_items(store, entries):
    """Write each item's record to `store` (this run's), one file per item key; written whole, then renamed."""
    if not store:
        return
    Path(store).mkdir(parents=True, exist_ok=True)
    for e in entries:
        tmp = Path(store) / f".{e['key']}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps({k: v for k, v in e.items() if k != "key"}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(Path(store) / f"{e['key']}.json")


def read_json_file(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail(f"cannot read {path}: {exc}")


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


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
        parts = artifact.get("parts") or []
        has_file = any("url" in p or "raw" in p for p in parts)
        for n, part in enumerate(parts):
            if has_file and "text" in part:
                continue  # a file's text beside the file itself (prometheus serve inlines small ones): one file, not two
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
        result, entries = merge(req)
        if (g := gate(req, req.get("timeout") or 600)) is not None:
            result["gate"] = g
        store_items(req.get("store"), entries)  # after the gate: a result it refused is never reused
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
