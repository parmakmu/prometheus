"""ui/egress_proxy.py, the one way out of a podman sandbox: who it lets through, and what it records. Runs the real
proxy on a free port; nothing here leaves the machine (refusals happen before any connection out)."""
import base64
import json
import socket
import subprocess
import sys
import time

import pytest
from conftest import ROOT


@pytest.fixture
def proxy(tmp_path):
    (tmp_path / "allow.json").write_text(json.dumps({"tok": {"agent": "ada/a/s", "hosts": ["example.com", ".pythonhosted.org"]}}))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen([sys.executable, ROOT / "ui" / "egress_proxy.py", tmp_path / "allow.json", tmp_path / "refused.jsonl", str(port)],
                            stdout=subprocess.PIPE, text=True)
    proc.stdout.readline()  # listening
    yield port, tmp_path
    proc.kill()


def ask(port, line, token=None):
    auth = f"Proxy-Authorization: Basic {base64.b64encode(f'{token}:x'.encode()).decode()}\r\n" if token else ""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(f"{line}\r\n{auth}\r\n".encode())
        return s.recv(200).decode().split("\r\n")[0]


def test_without_an_agents_token_nothing_passes(proxy):
    port, _ = proxy
    assert " 407 " in ask(port, "CONNECT example.com:443 HTTP/1.1")
    assert " 407 " in ask(port, "CONNECT example.com:443 HTTP/1.1", token="guessed")


def test_only_https_tunnels(proxy):
    port, _ = proxy
    assert " 405 " in ask(port, "GET http://example.com/ HTTP/1.1", token="tok")
    assert " 405 " in ask(port, "CONNECT example.com:22 HTTP/1.1", token="tok")


def test_a_host_not_allowed_is_refused_and_recorded(proxy):
    port, tmp = proxy
    assert " 403 " in ask(port, "CONNECT github.com:443 HTTP/1.1", token="tok")
    assert " 403 " in ask(port, "CONNECT example.com.evil.net:443 HTTP/1.1", token="tok")  # a suffix trick
    time.sleep(0.1)
    refused = [json.loads(line) for line in (tmp / "refused.jsonl").read_text().splitlines()]
    assert [(r["agent"], r["host"]) for r in refused] == [("ada/a/s", "github.com"), ("ada/a/s", "example.com.evil.net")]


def test_a_leading_dot_allows_subdomains_and_a_change_counts_at_once(proxy):
    port, tmp = proxy
    import importlib.util
    spec = importlib.util.spec_from_file_location("ep", ROOT / "ui" / "egress_proxy.py")
    assert spec and spec.loader
    sys.argv[1:] = [str(tmp / "allow.json"), str(tmp / "refused.jsonl")]
    ep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ep)
    assert ep.matches("files.pythonhosted.org", [".pythonhosted.org"]) and ep.matches("pythonhosted.org", [".pythonhosted.org"])
    assert not ep.matches("evilpythonhosted.org", [".pythonhosted.org"]) and not ep.matches("sub.example.com", ["example.com"])
    (tmp / "allow.json").write_text(json.dumps({"tok2": {"agent": "ada/a/t", "hosts": []}}))
    assert " 407 " in ask(port, "CONNECT example.com:443 HTTP/1.1", token="tok")  # reread on every connection
