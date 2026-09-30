"""DashScope model-access checks cover the complete displayed catalog."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

from vocal_more.application.dashscope_model_check import (
    DASHSCOPE_MODELS,
    _realtime_model_call,
    check_dashscope_models,
)


def test_checks_every_displayed_model_independently():
    calls = []

    def model_call(*, model: str, api_key: str):
        calls.append((model, api_key))
        return SimpleNamespace(status_code=200)

    results = check_dashscope_models(
        "sk-secret",
        model_call=model_call,
    )

    assert {(model, key) for model, key in calls} == {
        (model, "sk-secret") for _, model, _ in DASHSCOPE_MODELS
    }
    assert len(results) == 10
    assert [result["family"] for result in results].count("asr") == 6
    assert [result["family"] for result in results].count("llm") == 4
    assert all(result["status"] == "ok" for result in results)
    assert all(result["display_name"] for result in results)
    assert all("api_key" not in result for result in results)


def test_reports_provider_failure_without_hiding_other_models():
    def model_call(*, model: str, api_key: str):
        del api_key
        if model == "qwen3.7-plus":
            return SimpleNamespace(
                status_code=403,
                code="ModelAccessDenied",
                message="Model is not enabled",
            )
        return SimpleNamespace(status_code=200)

    results = check_dashscope_models(
        "sk-secret",
        model_call=model_call,
    )

    failed = next(result for result in results if result["model"] == "qwen3.7-plus")
    assert failed == {
        "family": "llm",
        "model": "qwen3.7-plus",
        "display_name": "Qwen 3.7 Plus",
        "status": "error",
        "latency_ms": failed["latency_ms"],
        "error": "ModelAccessDenied: Model is not enabled",
    }
    assert sum(result["status"] == "ok" for result in results) == 9


def test_missing_key_returns_an_error_for_every_model_without_calling_provider():
    def unexpected_call(**kwargs):
        raise AssertionError(kwargs)

    results = check_dashscope_models(
        "  ",
        model_call=unexpected_call,
    )

    assert len(results) == len(DASHSCOPE_MODELS) == 10
    assert all(result["status"] == "error" for result in results)
    assert all(result["error"] == "API key is missing" for result in results)


def test_provider_exception_cannot_echo_the_api_key():
    def model_call(*, model: str, api_key: str):
        raise RuntimeError(f"{model} rejected credential {api_key}")

    results = check_dashscope_models(
        "sk-secret",
        model_call=model_call,
    )

    assert all("sk-secret" not in result["error"] for result in results)
    assert all("***" in result["error"] for result in results)


@pytest.mark.parametrize(
    "model",
    [
        "qwen3.8-omni-flash-realtime",
        "qwen3.5-omni-plus-realtime",
        "qwen3.5-omni-flash-realtime",
    ],
)
def test_realtime_model_check_uses_catalog_transcription_submodel(monkeypatch, model):
    sent = []
    connection = {}

    class FakeWebSocket:
        def send(self, raw):
            sent.append(json.loads(raw))

        def recv(self):
            return json.dumps({"type": "session.updated"})

        def close(self):
            pass

    def create_connection(url, **_kwargs):
        connection["url"] = url
        return FakeWebSocket()

    monkeypatch.setitem(
        sys.modules,
        "websocket",
        SimpleNamespace(create_connection=create_connection),
    )

    response = _realtime_model_call(model=model, api_key="test-only")

    assert response.status_code == 200
    assert model in connection["url"]
    assert sent[0]["type"] == "session.update"
    assert sent[0]["session"]["input_audio_transcription"] == {
        "model": "qwen3-asr-flash-realtime"
    }
