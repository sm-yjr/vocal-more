"""DashScope model-access checks stay bounded and family-specific."""

from __future__ import annotations

from types import SimpleNamespace

from vocal_more.application.dashscope_model_check import (
    check_dashscope_model_families,
)


def test_checks_pro_and_lite_models_independently():
    calls = []

    def model_call(*, model: str, api_key: str):
        calls.append((model, api_key))
        return SimpleNamespace(status_code=200)

    results = check_dashscope_model_families(
        "sk-secret",
        model_call=model_call,
    )

    assert {(model, key) for model, key in calls} == {
        ("qwen3.5-omni-plus", "sk-secret"),
        ("qwen3.5-omni-flash", "sk-secret"),
    }
    assert [(result["family"], result["status"]) for result in results] == [
        ("pro", "ok"),
        ("lite", "ok"),
    ]
    assert all("api_key" not in result for result in results)


def test_reports_provider_failure_without_hiding_other_family():
    def model_call(*, model: str, api_key: str):
        del api_key
        if model.endswith("plus"):
            return SimpleNamespace(
                status_code=403,
                code="ModelAccessDenied",
                message="Model is not enabled",
            )
        return SimpleNamespace(status_code=200)

    results = check_dashscope_model_families(
        "sk-secret",
        model_call=model_call,
    )

    assert results[0] == {
        "family": "pro",
        "model": "qwen3.5-omni-plus",
        "status": "error",
        "latency_ms": results[0]["latency_ms"],
        "error": "ModelAccessDenied: Model is not enabled",
    }
    assert results[1]["status"] == "ok"


def test_missing_key_returns_two_errors_without_calling_provider():
    def unexpected_call(**kwargs):
        raise AssertionError(kwargs)

    results = check_dashscope_model_families(
        "  ",
        model_call=unexpected_call,
    )

    assert [result["family"] for result in results] == ["pro", "lite"]
    assert all(result["status"] == "error" for result in results)
    assert all(result["error"] == "API key is missing" for result in results)


def test_provider_exception_cannot_echo_the_api_key():
    def model_call(*, model: str, api_key: str):
        raise RuntimeError(f"{model} rejected credential {api_key}")

    results = check_dashscope_model_families(
        "sk-secret",
        model_call=model_call,
    )

    assert all("sk-secret" not in result["error"] for result in results)
    assert all("***" in result["error"] for result in results)
