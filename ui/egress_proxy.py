#!/usr/bin/env python3
"""The one way out of an agent's sandbox (SANDBOX=podman): an HTTPS proxy that lets each agent reach only the hosts
allowed for it, and records what it refused.

Workers sit on a network with no route out; their HTTPS_PROXY names this proxy with the agent's token. It takes
CONNECT only (HTTPS: it sees the host name, never the content) and nothing else.

  egress_proxy.py <allow.json> <refused.jsonl> [port]

allow.json, written by the router and reread on every connection: {"<token>": {"agent": "<user>/<slug>",
"hosts": ["api.anthropic.com", ".pythonhosted.org", ...]}}. A host matches itself; an entry starting with "." matches
the domain and every subdomain. Ports: 443 only. A refusal appends {"agent", "host", "at"} to refused.jsonl, which the
web app shows with an Allow button. Standard library only.
"""
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

ALLOW, REFUSED = Path(sys.argv[1]), Path(sys.argv[2])
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 3128


def allowed_for(token):
    try:
        return json.loads(ALLOW.read_text(encoding="utf-8")).get(token)
    except (OSError, ValueError):
        return None


def matches(host, entries):
    host = host.lower().rstrip(".")
    return any(host == e.lstrip(".") or (e.startswith(".") and host.endswith(e)) for e in (x.lower() for x in entries))


def token_of(headers):
    """The token from Proxy-Authorization: Basic base64(token:anything)."""
    value = headers.get("proxy-authorization", "")
    if not value.lower().startswith("basic "):
        return ""
    try:
        return base64.b64decode(value[6:].strip()).decode().split(":", 1)[0]
    except ValueError:
        return ""


def refuse(agent, host):
    with REFUSED.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"agent": agent, "host": host, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")


async def pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def handle(reader, writer):
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 30)
    except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        writer.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    method, target, *_ = lines[0].split(" ") + ["", ""]
    headers = {k.strip().lower(): v.strip() for k, _, v in (line.partition(":") for line in lines[1:] if ":" in line)}
    entry = allowed_for(token_of(headers))
    if entry is None:
        writer.write(b"HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic\r\n\r\n")
        writer.close()
        return
    host, _, port = target.rpartition(":")
    if method != "CONNECT" or port != "443" or not host:
        writer.write(b"HTTP/1.1 405 Method Not Allowed\r\n\r\nHTTPS (CONNECT to port 443) only\r\n")
        writer.close()
        return
    if not matches(host, entry.get("hosts") or []):
        refuse(entry.get("agent", "?"), host)
        writer.write(f"HTTP/1.1 403 Forbidden\r\n\r\n{host} is not allowed for this agent\r\n".encode())
        writer.close()
        return
    try:
        up_reader, up_writer = await asyncio.wait_for(asyncio.open_connection(host, 443), 20)
    except (TimeoutError, OSError):
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        writer.close()
        return
    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    await writer.drain()
    await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))


async def main():
    server = await asyncio.start_server(handle, "0.0.0.0", PORT)
    print(f"egress proxy on :{PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
