#!/usr/bin/env python3
"""Deterministic batching for map stages, run as the `workflow-batcher`
command-runner agent inside a compiled workflowScript.

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

Can also be run directly: batch_tool.py '<json request>'.
"""
import csv
import fnmatch
import io
import json
import os
import sys

MARKER = "BATCH_TOOL_REQUEST "


def fail(message):
    print(json.dumps({"error": message}))
    sys.exit(1)


def read_request(argv):
    if len(argv) > 1:
        return json.loads(argv[1])
    text = sys.stdin.read()
    lines = [l for l in text.splitlines() if l.strip().startswith(MARKER)]
    if not lines:
        fail(f"no {MARKER.strip()} line on stdin")
    return json.loads(lines[-1].strip()[len(MARKER):])


def items_from(source, glob):
    if os.path.isdir(source):
        names = sorted(n for n in os.listdir(source)
                       if os.path.isfile(os.path.join(source, n)) and fnmatch.fnmatch(n, glob or "*"))
        return [os.path.abspath(os.path.join(source, n)) for n in names], None
    if not os.path.isfile(source):
        fail(f"source not found: {source}")
    ext = os.path.splitext(source)[1].lower()
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
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
    os.makedirs(outdir, exist_ok=True)
    batches = []
    for index, start in enumerate(range(0, len(items), batch)):
        chunk = items[start:start + batch]
        if header is not None:
            path = os.path.abspath(os.path.join(outdir, f"{index}.csv"))
            with open(path, "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(header)
                w.writerows(chunk)
        else:
            path = os.path.abspath(os.path.join(outdir, f"{index}.txt"))
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(chunk) + "\n")
        batches.append({"index": index, "file": path, "count": len(chunk)})
    return {"total": len(items), "batches": batches}


def merge(req):
    inputs, output = req.get("inputs"), req.get("output")
    if not isinstance(inputs, list) or not output:
        fail("merge needs inputs (list) and output")
    missing = [p for p in inputs if not os.path.isfile(p)]
    if missing:
        fail(f"missing batch outputs: {missing}")
    ext = os.path.splitext(output)[1].lower()
    os.makedirs(os.path.dirname(os.path.abspath(output)) or ".", exist_ok=True)
    count = 0
    if ext == ".json":
        merged = []
        for p in inputs:
            with open(p, encoding="utf-8") as fh:
                text = fh.read()
            data = json.loads(text) if text.strip() else []
            merged.extend(data if isinstance(data, list) else [data])
        with open(output, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=1)
        count = len(merged)
    elif ext == ".csv":
        header, rows = None, []
        for p in inputs:
            with open(p, encoding="utf-8", newline="") as fh:
                data = list(csv.reader(fh))
            if not data:
                continue
            if header is None:
                header = data[0]
            elif data[0] != header:
                fail(f"{p} has a different CSV header")
            rows.extend(data[1:])
        with open(output, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            if header is not None:
                w.writerow(header)
            w.writerows(rows)
        count = len(rows)
    else:
        lines = []
        for p in inputs:
            with open(p, encoding="utf-8") as fh:
                lines.extend(l for l in fh.read().splitlines() if l.strip())
        with open(output, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + ("\n" if lines else ""))
        count = len(lines)
    return {"output": os.path.abspath(output), "count": count}


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
    else:
        fail(f"unknown op {op!r}")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
