"""JSON-RPC 2.0 over NDJSON stdin/stdout server.

This module captures the real stdout fd before any other code can write
to it, redirects sys.stdout to stderr, and provides thread-safe NDJSON
output on the captured fd.

Usage:
    echo '{"jsonrpc":"2.0","method":"initialize","params":{},"id":1}' | \
        python -u -m vocal_more.serve
"""

# === CRITICAL: Capture stdout fd BEFORE any import can print ===
import os
import sys

_raw_stdout_fd = os.dup(sys.stdout.fileno())
sys.stdout = sys.stderr  # All print() goes to stderr now

_jsonrpc_out = os.fdopen(_raw_stdout_fd, "w", buffering=1, closefd=True)

# === Now safe to import everything else ===
import json
import threading

from .bootstrap import build_rpc_handler
from .rpc_handler import RPCError, RPCHandler

_write_lock = threading.Lock()


def _send(msg: dict) -> None:
    """Write a JSON-RPC message to the captured stdout (thread-safe)."""
    line = json.dumps(msg, ensure_ascii=False)
    with _write_lock:
        _jsonrpc_out.write(line + "\n")
        _jsonrpc_out.flush()


def send_notification(method: str, params: dict) -> None:
    """Send a JSON-RPC notification (no id)."""
    _send({"jsonrpc": "2.0", "method": method, "params": params})


def _make_response(id, result) -> dict:
    return {"jsonrpc": "2.0", "result": result, "id": id}


def _make_error(id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": id}


def main() -> None:
    """Main entry point: read stdin line by line, dispatch, respond."""
    handler = build_rpc_handler(
        send_notification=send_notification,
        handler_factory=RPCHandler,
    )
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            # Parse JSON
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as e:
                _send(_make_error(None, -32700, f"Parse error: {e}"))
                continue

            # Validate JSON-RPC structure
            if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                msg_id = msg.get("id") if isinstance(msg, dict) else None
                _send(_make_error(msg_id, -32600, "Invalid JSON-RPC 2.0 request"))
                continue

            method = msg.get("method")
            params = msg.get("params", {})
            msg_id = msg.get("id")

            if not isinstance(method, str):
                _send(_make_error(msg_id, -32600, "Missing or invalid method"))
                continue

            # Notifications (no id) — fire and forget
            if msg_id is None:
                try:
                    handler.dispatch(method, params)
                except Exception:
                    pass
                continue

            # Requests (with id) — must respond
            try:
                result = handler.dispatch(method, params)
                _send(_make_response(msg_id, result))
            except RPCError as e:
                _send(_make_error(msg_id, e.code, e.message))
            except Exception as e:
                _send(_make_error(msg_id, -32603, f"Internal error: {e}"))
    finally:
        close = getattr(handler, "close", None)
        if callable(close):
            close()

    # stdin closed (Swift closed the pipe) — exit cleanly


if __name__ == "__main__":
    main()
