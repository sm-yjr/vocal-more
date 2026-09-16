"""Bounded, per-model DashScope access checks for the settings UI."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from http import HTTPStatus
from typing import Any
from urllib.parse import urlencode

from ..domain.model_catalog import (
    ASR_MODEL_CATALOG,
    LLM_MODEL_CATALOG,
    get_asr_model_info,
)

DASHSCOPE_MODELS = tuple(
    ("asr", model["id"], model["display_name"])
    for model in ASR_MODEL_CATALOG
) + tuple(
    ("llm", model["id"], model["display_name"])
    for model in LLM_MODEL_CATALOG
)


@dataclass(frozen=True)
class DashScopeModelCheckResult:
    family: str
    model: str
    display_name: str
    status: str
    latency_ms: int
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _realtime_model_call(*, model: str, api_key: str):
    import websocket

    info = get_asr_model_info(model) or {}
    recognition = info.get("protocol") == "audio_recognition"
    endpoint = (
        "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
        if recognition
        else "wss://dashscope.aliyuncs.com/api-ws/v1/realtime?"
        + urlencode({"model": model})
    )
    socket = websocket.create_connection(
        endpoint,
        header=[f"Authorization: Bearer {api_key}"],
        timeout=10,
    )
    try:
        task_id = uuid.uuid4().hex
        if recognition:
            event = {
                "header": {
                    "action": "run-task",
                    "task_id": task_id,
                    "streaming": "duplex",
                },
                "payload": {
                    "task_group": "audio",
                    "task": "asr",
                    "function": "recognition",
                    "model": model,
                    "parameters": {
                        "format": "pcm",
                        "sample_rate": 16000,
                        "heartbeat": True,
                    },
                    "input": {},
                },
            }
        else:
            session = {
                "modalities": ["text"],
                "voice": info.get("voice", "Tina"),
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": None,
                "turn_detection": None,
                "instructions": "Return transcription text only.",
            }
            if info.get("protocol") != "realtime_conversation":
                session["input_audio_transcription"] = {
                    "model": "gummy-realtime-v1"
                }
            event = {
                "event_id": f"event_{uuid.uuid4().hex}",
                "type": "session.update",
                "session": session,
            }
        socket.send(json.dumps(event))
        while True:
            response = json.loads(socket.recv())
            header_event = response.get("header", {}).get("event")
            if response.get("type") == "error" or header_event == "task-failed":
                raise RuntimeError("DashScope realtime session was rejected")
            if recognition and header_event == "task-started":
                return type("Response", (), {"status_code": HTTPStatus.OK})()
            if not recognition and response.get("type") == "session.updated":
                return type("Response", (), {"status_code": HTTPStatus.OK})()
    finally:
        socket.close()


def _default_model_call(*, model: str, api_key: str):
    if get_asr_model_info(model) is not None:
        return _realtime_model_call(model=model, api_key=api_key)

    from dashscope import MultiModalConversation

    return MultiModalConversation.call(
        model=model,
        api_key=api_key,
        messages=[{"role": "user", "content": [{"text": "Reply with OK."}]}],
        enable_thinking=False,
        max_tokens=1,
        timeout=10,
    )


def _safe_provider_error(response: object) -> str:
    code = str(getattr(response, "code", "") or "").strip()
    message = str(getattr(response, "message", "") or "").strip()
    error = ": ".join(part for part in (code, message) if part)
    return error[:300] or "DashScope request failed"


def _safe_exception_error(exc: Exception, api_key: str) -> str:
    error = str(exc).strip().replace(api_key, "***")
    return error[:300] or type(exc).__name__


def _check_one(
    family: str,
    model: str,
    display_name: str,
    api_key: str,
    model_call: Callable[..., object],
) -> DashScopeModelCheckResult:
    started_at = time.perf_counter()
    try:
        response = model_call(model=model, api_key=api_key)
        status_code = int(getattr(response, "status_code", 0) or 0)
        status = "ok" if status_code == HTTPStatus.OK else "error"
        error = "" if status == "ok" else _safe_provider_error(response)
    except Exception as exc:  # noqa: BLE001 - provider transports expose varied errors
        status = "error"
        error = _safe_exception_error(exc, api_key)

    return DashScopeModelCheckResult(
        family=family,
        model=model,
        display_name=display_name,
        status=status,
        latency_ms=max(0, round((time.perf_counter() - started_at) * 1000)),
        error=error,
    )


def check_dashscope_models(
    api_key: str,
    *,
    model_call: Callable[..., object] | None = None,
) -> list[dict[str, Any]]:
    """Check every model exposed by the settings catalog, four at a time."""
    key = str(api_key or "").strip()
    if not key:
        return [
            DashScopeModelCheckResult(
                family=family,
                model=model,
                display_name=display_name,
                status="error",
                latency_ms=0,
                error="API key is missing",
            ).to_dict()
            for family, model, display_name in DASHSCOPE_MODELS
        ]

    call = model_call or _default_model_call
    with ThreadPoolExecutor(
        max_workers=min(4, len(DASHSCOPE_MODELS)),
        thread_name_prefix="vocal-more-dashscope-check",
    ) as executor:
        futures = [
            executor.submit(_check_one, family, model, name, key, call)
            for family, model, name in DASHSCOPE_MODELS
        ]
        return [future.result().to_dict() for future in futures]


# Compatibility name for callers outside the settings window.
check_dashscope_model_families = check_dashscope_models
