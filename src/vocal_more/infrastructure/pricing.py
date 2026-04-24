"""Alibaba Cloud pricing helpers for ASR and text polishing costs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


CHINA_MAINLAND_REGION = "cn-beijing"

_ASR_AUDIO_SECONDS_PRICING = {
    "qwen3-asr-flash": 0.00022,
    "qwen3-asr-flash-2026-02-10": 0.00022,
    "qwen3-asr-flash-2025-09-08": 0.00022,
    "qwen3-asr-flash-realtime": 0.00033,
    "qwen3-asr-flash-realtime-2026-02-10": 0.00033,
    "qwen3-asr-flash-realtime-2025-10-27": 0.00033,
}

_OMNI_PRICING = {
    "qwen3.5-omni-plus": {
        "input_text": 7.0,
        "input_audio": 53.0,
        "output_text": 40.0,
        "output_audio": 213.0,
    },
    "qwen3.5-omni-flash": {
        "input_text": 2.2,
        "input_audio": 18.0,
        "output_text": 13.3,
        "output_audio": 72.0,
    },
    "qwen3.5-omni-plus-realtime": {
        "input_text": 10.0,
        "input_audio": 80.0,
        "output_text": 60.0,
        "output_audio": 300.0,
    },
    "qwen3.5-omni-flash-realtime": {
        "input_text": 3.3,
        "input_audio": 27.0,
        "output_text": 20.0,
        "output_audio": 107.0,
    },
}

_TEXT_TIERED_PRICING = {
    "qwen3.5-plus": [
        {
            "max_prompt_tokens": 128 * 1024,
            "input_non_thinking": 0.8,
            "input_thinking": 4.8,
            "output": 4.8,
        },
        {
            "max_prompt_tokens": 256 * 1024,
            "input_non_thinking": 2.0,
            "input_thinking": 12.0,
            "output": 12.0,
        },
        {
            "max_prompt_tokens": 1_000_000,
            "input_non_thinking": 4.0,
            "input_thinking": 24.0,
            "output": 24.0,
        },
    ],
    "qwen3.6-plus": [
        {
            "max_prompt_tokens": 256 * 1024,
            "input_non_thinking": 2.0,
            "input_thinking": 12.0,
            "output": 12.0,
        },
        {
            "max_prompt_tokens": 1_000_000,
            "input_non_thinking": 8.0,
            "input_thinking": 48.0,
            "output": 48.0,
        },
    ],
}


def _safe_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_float(value: object) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _round_cost(value: float) -> float:
    return round(max(0.0, value), 6)


def _normalize_usage_details(details: object) -> dict[str, int]:
    if details is None:
        return {}
    if isinstance(details, dict):
        source = details
    else:
        source = {
            key: getattr(details, key)
            for key in dir(details)
            if not key.startswith("_") and not callable(getattr(details, key))
        }
    normalized: dict[str, int] = {}
    for key, value in source.items():
        normalized[str(key)] = _safe_int(value)
    return normalized


def normalize_token_usage(raw_usage: object) -> dict[str, Any] | None:
    """Normalize usage payloads from DashScope/OpenAI-compatible responses."""
    if raw_usage is None:
        return None

    if isinstance(raw_usage, dict):
        source = raw_usage
    else:
        source = {
            key: getattr(raw_usage, key)
            for key in dir(raw_usage)
            if not key.startswith("_") and not callable(getattr(raw_usage, key))
        }

    prompt_tokens = _safe_int(
        source.get("prompt_tokens", source.get("input_tokens"))
    )
    completion_tokens = _safe_int(
        source.get("completion_tokens", source.get("output_tokens"))
    )
    total_tokens = _safe_int(source.get("total_tokens"))
    if total_tokens == 0:
        total_tokens = prompt_tokens + completion_tokens

    prompt_details = _normalize_usage_details(
        source.get("prompt_tokens_details", source.get("input_tokens_details"))
    )
    completion_details = _normalize_usage_details(
        source.get("completion_tokens_details", source.get("output_tokens_details"))
    )

    normalized = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "prompt_tokens_details": prompt_details,
        "completion_tokens_details": completion_details,
    }
    if any(key in source for key in ("input_tokens", "output_tokens")):
        normalized["input_tokens"] = prompt_tokens
        normalized["output_tokens"] = completion_tokens
        normalized["input_tokens_details"] = deepcopy(prompt_details)
        normalized["output_tokens_details"] = deepcopy(completion_details)
    return normalized


def extract_usage_from_response(response: object) -> dict[str, Any] | None:
    """Best-effort usage extraction for DashScope style responses."""
    if response is None:
        return None

    direct = normalize_token_usage(getattr(response, "usage", None))
    if direct is not None:
        return direct

    output = getattr(response, "output", None)
    if output is None:
        return None
    return normalize_token_usage(getattr(output, "usage", None))


def estimate_omni_audio_tokens(seconds: float, *, output: bool = False) -> int:
    rate = 12.5 if output else 7.0
    seconds = _safe_float(seconds)
    if seconds <= 0:
        return 0
    return max(1, int(round(max(1.0, seconds) * rate)))


def build_asr_billing(
    *,
    model: str,
    audio_seconds: float,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build billing metadata for an ASR request."""
    seconds = _safe_float(audio_seconds)
    normalized_usage = normalize_token_usage(usage)

    if model in _ASR_AUDIO_SECONDS_PRICING:
        unit_price = _ASR_AUDIO_SECONDS_PRICING[model]
        cost = _round_cost(seconds * unit_price)
        return {
            "stage": "asr",
            "model": model,
            "region": CHINA_MAINLAND_REGION,
            "pricing_basis": "audio_seconds",
            "audio_seconds": round(seconds, 3),
            "unit_price_cny_per_second": unit_price,
            "cost_cny": cost,
            "estimated": False,
            "usage": None,
        }

    omni_pricing = _OMNI_PRICING.get(model)
    if omni_pricing is None:
        return None

    estimated = normalized_usage is None
    if normalized_usage is None:
        input_audio_tokens = estimate_omni_audio_tokens(seconds)
        normalized_usage = {
            "input_tokens": input_audio_tokens,
            "output_tokens": 0,
            "total_tokens": input_audio_tokens,
            "input_tokens_details": {"audio_tokens": input_audio_tokens},
            "output_tokens_details": {},
        }

    input_details = deepcopy(normalized_usage.get("input_tokens_details", {}))
    output_details = deepcopy(normalized_usage.get("output_tokens_details", {}))
    input_text_tokens = _safe_int(input_details.get("text_tokens"))
    input_audio_tokens = _safe_int(input_details.get("audio_tokens"))
    output_text_tokens = _safe_int(output_details.get("text_tokens"))
    output_audio_tokens = _safe_int(output_details.get("audio_tokens"))

    input_text_cost = input_text_tokens * omni_pricing["input_text"] / 1_000_000
    input_audio_cost = input_audio_tokens * omni_pricing["input_audio"] / 1_000_000
    output_text_cost = output_text_tokens * omni_pricing["output_text"] / 1_000_000
    output_audio_cost = output_audio_tokens * omni_pricing["output_audio"] / 1_000_000
    total_cost = _round_cost(
        input_text_cost + input_audio_cost + output_text_cost + output_audio_cost
    )

    return {
        "stage": "asr",
        "model": model,
        "region": CHINA_MAINLAND_REGION,
        "pricing_basis": "token_usage",
        "audio_seconds": round(seconds, 3),
        "cost_cny": total_cost,
        "estimated": estimated,
        "usage": normalized_usage,
        "cost_breakdown_cny": {
            "input_text": _round_cost(input_text_cost),
            "input_audio": _round_cost(input_audio_cost),
            "output_text": _round_cost(output_text_cost),
            "output_audio": _round_cost(output_audio_cost),
        },
    }


def build_polish_billing(
    *,
    model: str,
    enable_thinking: bool,
    usage: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Build billing metadata for second-stage text polishing."""
    normalized_usage = normalize_token_usage(usage)
    tiers = _TEXT_TIERED_PRICING.get(model)
    if normalized_usage is None or tiers is None:
        return None

    prompt_tokens = _safe_int(normalized_usage.get("prompt_tokens"))
    completion_tokens = _safe_int(normalized_usage.get("completion_tokens"))
    chosen_tier = tiers[-1]
    for tier in tiers:
        if prompt_tokens <= int(tier["max_prompt_tokens"]):
            chosen_tier = tier
            break

    input_price = (
        chosen_tier["input_thinking"] if enable_thinking else chosen_tier["input_non_thinking"]
    )
    output_price = chosen_tier["output"]
    input_cost = prompt_tokens * input_price / 1_000_000
    output_cost = completion_tokens * output_price / 1_000_000
    total_cost = _round_cost(input_cost + output_cost)

    return {
        "stage": "polish",
        "model": model,
        "region": CHINA_MAINLAND_REGION,
        "pricing_basis": "token_usage",
        "cost_cny": total_cost,
        "estimated": False,
        "usage": normalized_usage,
        "thinking_enabled": bool(enable_thinking),
        "input_price_cny_per_million": input_price,
        "output_price_cny_per_million": output_price,
        "cost_breakdown_cny": {
            "input": _round_cost(input_cost),
            "output": _round_cost(output_cost),
        },
    }


def merge_billing(*items: dict[str, Any] | None) -> dict[str, Any] | None:
    """Merge stage-level billing entries into a recording-level summary."""
    entries = [deepcopy(item) for item in items if item]
    if not entries:
        return None

    asr_cost = _round_cost(
        sum(item.get("cost_cny", 0.0) for item in entries if item.get("stage") == "asr")
    )
    polish_cost = _round_cost(
        sum(item.get("cost_cny", 0.0) for item in entries if item.get("stage") == "polish")
    )
    total_cost = _round_cost(asr_cost + polish_cost)

    merged = {
        "currency": "CNY",
        "region": CHINA_MAINLAND_REGION,
        "total_cost_cny": total_cost,
        "asr_cost_cny": asr_cost,
        "polish_cost_cny": polish_cost,
        "estimated": any(bool(item.get("estimated")) for item in entries),
    }
    for item in entries:
        stage = item.get("stage")
        if isinstance(stage, str) and stage:
            merged[stage] = item
    return merged
