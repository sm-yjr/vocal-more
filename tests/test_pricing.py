"""Tests for pricing helpers."""

from vocal_more.infrastructure.pricing import (
    build_asr_billing,
    build_polish_billing,
    merge_billing,
)


def test_asr_second_based_models_use_official_per_second_price():
    billing = build_asr_billing(
        model="qwen3-asr-flash-realtime-2026-02-10",
        audio_seconds=10.0,
    )

    assert billing is not None
    assert billing["pricing_basis"] == "audio_seconds"
    assert billing["cost_cny"] == 0.0033
    assert billing["estimated"] is False


def test_omni_realtime_uses_usage_breakdown_when_available():
    billing = build_asr_billing(
        model="qwen3.5-omni-plus-realtime",
        audio_seconds=5.2,
        usage={
            "input_tokens": 44,
            "output_tokens": 30,
            "total_tokens": 74,
            "input_tokens_details": {"audio_tokens": 37, "text_tokens": 7},
            "output_tokens_details": {"text_tokens": 30},
        },
    )

    assert billing is not None
    assert billing["pricing_basis"] == "token_usage"
    assert billing["estimated"] is False
    assert billing["cost_breakdown_cny"]["input_audio"] == 0.00296
    assert billing["cost_breakdown_cny"]["input_text"] == 0.00007
    assert billing["cost_breakdown_cny"]["output_text"] == 0.0018
    assert billing["cost_cny"] == 0.00483


def test_omni_estimates_audio_tokens_when_usage_missing():
    billing = build_asr_billing(
        model="qwen3.5-omni-flash",
        audio_seconds=2.0,
    )

    assert billing is not None
    assert billing["estimated"] is True
    assert billing["usage"]["input_tokens_details"]["audio_tokens"] == 14


def test_polish_billing_uses_prompt_tier_and_thinking_flag():
    billing = build_polish_billing(
        model="qwen3.5-plus",
        enable_thinking=True,
        usage={
            "prompt_tokens": 1000,
            "completion_tokens": 100,
            "total_tokens": 1100,
        },
    )

    assert billing is not None
    assert billing["input_price_cny_per_million"] == 4.8
    assert billing["output_price_cny_per_million"] == 4.8
    assert billing["cost_cny"] == 0.00528


def test_merge_billing_rolls_up_total_and_stage_costs():
    merged = merge_billing(
        {"stage": "asr", "cost_cny": 0.002, "estimated": True},
        {"stage": "polish", "cost_cny": 0.001, "estimated": False},
    )

    assert merged == {
        "currency": "CNY",
        "region": "cn-beijing",
        "total_cost_cny": 0.003,
        "asr_cost_cny": 0.002,
        "polish_cost_cny": 0.001,
        "estimated": True,
        "asr": {"stage": "asr", "cost_cny": 0.002, "estimated": True},
        "polish": {"stage": "polish", "cost_cny": 0.001, "estimated": False},
    }
