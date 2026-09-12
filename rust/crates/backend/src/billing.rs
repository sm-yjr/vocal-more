// SPDX-License-Identifier: GPL-3.0-only
//! Price snapshot from the shipped Python backend, not a live billing quote.
use crate::catalog::CONTRACT;
use serde_json::{Value, json};

fn number(v: &Value) -> f64 {
    v.as_f64()
        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
        .filter(|n| n.is_finite())
        .unwrap_or(0.0)
        .max(0.0)
}
fn integer(v: &Value) -> u64 {
    number(v) as u64
}
fn rounded(value: f64, places: i32) -> f64 {
    // Format the original binary float directly; multiplying by 10^n first
    // loses the low bit near half-way values (Python round uses exact digits).
    format!("{:.*}", places as usize, value.max(0.0))
        .parse()
        .unwrap_or(0.0)
}
fn details(value: &Value) -> Value {
    Value::Object(
        value
            .as_object()
            .map(|o| {
                o.iter()
                    .map(|(k, v)| (k.clone(), json!(integer(v))))
                    .collect()
            })
            .unwrap_or_default(),
    )
}
pub fn normalize_usage(raw: &Value) -> Value {
    if raw.is_null() {
        return Value::Null;
    }
    let input = integer(raw.get("prompt_tokens").unwrap_or(&raw["input_tokens"]));
    let output = integer(
        raw.get("completion_tokens")
            .unwrap_or(&raw["output_tokens"]),
    );
    let total = integer(&raw["total_tokens"]);
    let input_details = details(
        raw.get("prompt_tokens_details")
            .unwrap_or(&raw["input_tokens_details"]),
    );
    let output_details = details(
        raw.get("completion_tokens_details")
            .unwrap_or(&raw["output_tokens_details"]),
    );
    let mut value = json!({"prompt_tokens":input,"completion_tokens":output,
        "total_tokens":if total == 0 {input + output} else {total},
        "prompt_tokens_details":input_details,"completion_tokens_details":output_details});
    if raw.get("input_tokens").is_some() || raw.get("output_tokens").is_some() {
        value["input_tokens"] = json!(input);
        value["output_tokens"] = json!(output);
        value["input_tokens_details"] = input_details;
        value["output_tokens_details"] = output_details;
    }
    value
}
pub fn asr(model: &str, seconds: f64, raw_usage: &Value) -> Value {
    let seconds = if seconds.is_finite() {
        seconds.max(0.0)
    } else {
        0.0
    };
    let prices = &CONTRACT["pricing"];
    if let Some(rate) = prices["asr_seconds"][model].as_f64() {
        return json!({"stage":"asr","model":model,"region":"cn-beijing",
            "pricing_basis":"audio_seconds","audio_seconds":rounded(seconds,3),
            "unit_price_cny_per_second":rate,"cost_cny":rounded(seconds*rate,6),"estimated":false,"usage":null});
    }
    let Some(prices) = prices["omni"][model].as_object() else {
        return Value::Null;
    };
    let mut usage = normalize_usage(raw_usage);
    let estimated = usage.is_null();
    if estimated {
        let tokens = if seconds > 0.0 {
            (seconds.max(1.0) * 7.0).round_ties_even().max(1.0) as u64
        } else {
            0
        };
        usage = json!({"input_tokens":tokens,"output_tokens":0,"total_tokens":tokens,
            "input_tokens_details":{"audio_tokens":tokens},"output_tokens_details":{}});
    }
    let mut costs = json!({});
    let mut total = 0.0;
    for (name, direction, modality) in [
        ("input_text", "input", "text"),
        ("input_audio", "input", "audio"),
        ("output_text", "output", "text"),
        ("output_audio", "output", "audio"),
    ] {
        let cost =
            integer(&usage[format!("{direction}_tokens_details")][format!("{modality}_tokens")])
                as f64
                * number(&prices[name])
                / 1_000_000.0;
        costs[name] = json!(rounded(cost, 6));
        total += cost;
    }
    json!({"stage":"asr","model":model,"region":"cn-beijing","pricing_basis":"token_usage",
        "audio_seconds":rounded(seconds,3),"cost_cny":rounded(total,6),"estimated":estimated,"usage":usage,
        "cost_breakdown_cny":costs})
}
pub fn polish(model: &str, thinking: bool, raw_usage: &Value) -> Value {
    let usage = normalize_usage(raw_usage);
    let Some(tiers) = CONTRACT["pricing"]["text"][model].as_array() else {
        return Value::Null;
    };
    if usage.is_null() {
        return Value::Null;
    }
    let input = integer(&usage["prompt_tokens"]);
    let output = integer(&usage["completion_tokens"]);
    let tier = tiers
        .iter()
        .find(|t| input <= integer(&t["max_prompt_tokens"]))
        .or_else(|| tiers.last())
        .unwrap();
    let input_price = number(
        &tier[if thinking {
            "input_thinking"
        } else {
            "input_non_thinking"
        }],
    );
    let output_price = number(&tier["output"]);
    let input_cost = input as f64 * input_price / 1_000_000.0;
    let output_cost = output as f64 * output_price / 1_000_000.0;
    json!({"stage":"polish","model":model,"region":"cn-beijing","pricing_basis":"token_usage",
        "cost_cny":rounded(input_cost+output_cost,6),"estimated":false,"usage":usage,
        "thinking_enabled":thinking,"input_price_cny_per_million":input_price,"output_price_cny_per_million":output_price,
        "cost_breakdown_cny":{"input":rounded(input_cost,6),"output":rounded(output_cost,6)}})
}
pub fn merge(items: &[Value]) -> Value {
    let items: Vec<_> = items.iter().filter(|i| i.is_object()).collect();
    if items.is_empty() {
        return Value::Null;
    }
    let cost = |stage: &str| {
        rounded(
            items
                .iter()
                .filter(|i| i["stage"] == stage)
                .map(|i| number(&i["cost_cny"]))
                .sum(),
            6,
        )
    };
    let (asr, polish) = (cost("asr"), cost("polish"));
    let mut merged = json!({"currency":"CNY","region":"cn-beijing","total_cost_cny":rounded(asr+polish,6),
        "asr_cost_cny":asr,"polish_cost_cny":polish,"estimated":items.iter().any(|i| i["estimated"] == true)});
    for item in items {
        if let Some(stage) = item["stage"].as_str().filter(|s| !s.is_empty()) {
            merged[stage] = item.clone();
        }
    }
    merged
}
