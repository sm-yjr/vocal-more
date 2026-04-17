"""Integration tests for the JSON-RPC stdin/stdout server."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DASHSCOPE_API_KEY", "test-api-key")

PROJECT_ROOT = Path(__file__).parent.parent
SRC_PATH = PROJECT_ROOT / "src"
SUBPROCESS_STUBS = PROJECT_ROOT / "tests" / "subprocess_stubs"


def _serve_env() -> dict[str, str]:
    pythonpath = os.environ.get("PYTHONPATH", "")
    entries = [str(SUBPROCESS_STUBS), str(SRC_PATH)]
    if pythonpath:
        entries.append(pythonpath)
    return {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "PYTHONPATH": os.pathsep.join(entries),
    }


def _run_serve(input_lines: list[str], timeout: float = 10.0) -> list[dict]:
    """Run vocal-more-serve as a subprocess with given JSON input lines.

    Returns parsed JSON response objects.
    """
    input_text = "\n".join(input_lines) + "\n"
    result = subprocess.run(
        [sys.executable, "-u", "-m", "vocal_more.serve"],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=PROJECT_ROOT,
        env=_serve_env(),
    )
    responses = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line:
            responses.append(json.loads(line))
    return responses


def test_serve_initialize_roundtrip():
    """Send initialize request and verify valid JSON-RPC response."""
    req = json.dumps({"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1})
    responses = _run_serve([req])

    assert len(responses) >= 1
    resp = responses[0]
    assert resp["jsonrpc"] == "2.0"
    assert resp["id"] == 1
    assert "result" in resp
    assert resp["result"]["version"] == "0.2.0"
    assert resp["result"]["state"] == "idle"


def test_serve_stdin_eof_exit():
    """Verify the process exits cleanly when stdin is closed."""
    req = json.dumps({"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1})
    result = subprocess.run(
        [sys.executable, "-u", "-m", "vocal_more.serve"],
        input=req + "\n",
        capture_output=True,
        text=True,
        timeout=10.0,
        cwd=PROJECT_ROOT,
        env=_serve_env(),
    )
    assert result.returncode == 0


def test_serve_parse_error():
    """Send invalid JSON and verify -32700 Parse error response."""
    responses = _run_serve(["this is not json"])

    assert len(responses) >= 1
    resp = responses[0]
    assert resp["jsonrpc"] == "2.0"
    assert "error" in resp
    assert resp["error"]["code"] == -32700


def test_serve_stdout_isolation():
    """Verify that Python print() output does NOT appear in stdout.

    All print() output should be redirected to stderr by serve.py.
    """
    # Send a request that triggers mode state changes which cause print() in walkie_talkie.py
    req = json.dumps({"jsonrpc": "2.0", "method": "initialize", "params": {}, "id": 1})
    result = subprocess.run(
        [sys.executable, "-u", "-m", "vocal_more.serve"],
        input=req + "\n",
        capture_output=True,
        text=True,
        timeout=10.0,
        cwd=PROJECT_ROOT,
        env=_serve_env(),
    )
    # Every line in stdout must be valid JSON
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line:
            parsed = json.loads(line)  # Should not raise
            assert "jsonrpc" in parsed


def test_serve_main_builds_handler_via_bootstrap(monkeypatch):
    """serve.main should create its RPC handler through bootstrap."""
    import importlib
    import io

    serve_module = importlib.import_module("vocal_more.serve")
    serve_module = importlib.reload(serve_module)

    built = {}

    class FakeHandler:
        def dispatch(self, method, params):
            return {"ok": True}

    monkeypatch.setattr(
        serve_module,
        "build_rpc_handler",
        lambda *, send_notification, handler_factory: built.setdefault("handler", FakeHandler()),
    )
    monkeypatch.setattr(serve_module, "_send", lambda msg: built.setdefault("sent", []).append(msg))
    monkeypatch.setattr(
        serve_module,
        "sys",
        type(
            "FakeSys",
            (),
            {
                "stdin": io.StringIO(
                    '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}\n'
                ),
            },
        ),
    )

    serve_module.main()

    assert "handler" in built
    assert built["sent"][0]["result"] == {"ok": True}
