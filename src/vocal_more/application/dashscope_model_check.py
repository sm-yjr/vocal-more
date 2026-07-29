"""Small, explicit DashScope model-access checks for the settings UI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from http import HTTPStatus
import time
from typing import Any, Callable


DASHSCOPE_MODEL_FAMILIES = {
    "pro": "qwen3.5-omni-plus",
    "lite": "qwen3.5-omni-flash",
}


@dataclass(frozen=True)
class DashScopeModelCheckResult:
    family: str
    model: str
    status: str
    latency_ms: int
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _default_model_call(*, model: str, api_key: str):
    from dashscope import MultiModalConversation

    return MultiModalConversation.call(
        model=model,
        api_key=api_key,
        messages=[
            {
                "role": "user",
                "content": [{"text": "Reply with OK."}],
            }
        ],
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
    api_key: str,
    model_call: Callable[..., object],
) -> DashScopeModelCheckResult:
    started_at = time.perf_counter()
    try:
        response = model_call(model=model, api_key=api_key)
        status_code = int(getattr(response, "status_code", 0) or 0)
        if status_code == HTTPStatus.OK:
            status = "ok"
            error = ""
        else:
            status = "error"
            error = _safe_provider_error(response)
    except Exception as exc:
        status = "error"
        error = _safe_exception_error(exc, api_key)

    return DashScopeModelCheckResult(
        family=family,
        model=model,
        status=status,
        latency_ms=max(0, round((time.perf_counter() - started_at) * 1000)),
        error=error,
    )


def check_dashscope_model_families(
    api_key: str,
    *,
    model_call: Callable[..., object] | None = None,
) -> list[dict[str, Any]]:
    """Check Pro and Lite access independently without exposing the API key."""
    key = str(api_key or "").strip()
    if not key:
        return [
            DashScopeModelCheckResult(
                family=family,
                model=model,
                status="error",
                latency_ms=0,
                error="API key is missing",
            ).to_dict()
            for family, model in DASHSCOPE_MODEL_FAMILIES.items()
        ]

    call = model_call or _default_model_call
    with ThreadPoolExecutor(
        max_workers=len(DASHSCOPE_MODEL_FAMILIES),
        thread_name_prefix="vocal-more-dashscope-check",
    ) as executor:
        futures = [
            executor.submit(_check_one, family, model, key, call)
            for family, model in DASHSCOPE_MODEL_FAMILIES.items()
        ]
        return [future.result().to_dict() for future in futures]
