#!/usr/bin/env python3
"""The `workflow-batcher` command-runner agent (no model) inside a compiled
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

merge  {"op":"merge","inputs":[PATH,...],"output":PATH}
       Concatenates by the output's extension: .json (arrays are flattened,
       objects appended), .jsonl / .txt (lines), .csv (header once).
       Missing inputs are an error. Prints {"output":PATH,"count":N}.

run    {"op":"run","command":CMD,"gate":CMD?,"gate_json":BOOL?}
       Runs a stage's program, then its gate, each through the shell (so
       ${SECRET} variables expand, as in gates). Prints {"program": {...},
       "gate": {...}}; the gate's JSON when gate_json. A failing program or
       gate exits 1 with the reason on stderr: pi-subagents fails the child
       with it, and the wave hands the reason to the stage's fix child.

Can also be run directly: batch_tool.py '<json request>'.
"""
import csv
import fnmatch
import io
import json
import subprocess
import sys
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
        count = len(merged)
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
        count = len(rows)
    else:
        lines = []
        for p in inputs:
            lines.extend(l for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip())
        output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        count = len(lines)
    return {"output": str(output.resolve()), "count": count}


def run(req):
    command, gate = req.get("command"), req.get("gate")
    if not command:
        fail("run needs command")
    p = subprocess.run(command, shell=True, capture_output=True, text=True, check=False)
    if p.returncode != 0:
        fail(f"the program exited with code {p.returncode}: {tail(p.stderr or p.stdout)}")
    result = {"program": {"exit": 0, "stdout": tail(p.stdout)}}
    if gate:
        g = subprocess.run(gate, shell=True, capture_output=True, text=True, check=False)
        if g.returncode != 0:
            fail(f"the program ran, but its gate failed (exit code {g.returncode}): {tail(g.stderr or g.stdout)}")
        if req.get("gate_json"):
            try:
                result["gate"] = json.loads(g.stdout)
            except ValueError:
                fail(f"the program ran, but its gate did not print JSON: {tail(g.stdout)}")
        else:
            result["gate"] = {"exit": 0}
    return result


def main(argv):
    try:
        req = read_request(argv)
    except ValueError as exc:
        fail(f"bad request JSON: {exc}")
    op = req.get("op")
    if op == "split":
        result = split(req)
    elif op == "merge":
        result = merge(req)
    elif op == "run":
        result = run(req)
    else:
        fail(f"unknown op {op!r}")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
