// SPDX-License-Identifier: GPL-3.0-only
use serde_json::Value;
use std::sync::LazyLock;

/// Versioned product constants. Generated from the Python reference only by a
/// developer tool; embedded in the executable, with no Python runtime dependency.
pub static CONTRACT: LazyLock<Value> = LazyLock::new(|| {
    serde_json::from_str(include_str!("../assets/product-contract.json"))
        .expect("validated product contract")
});

pub fn asr_model(id: &str) -> Option<&'static Value> {
    CONTRACT["all_asr_models"]
        .as_array()?
        .iter()
        .find(|m| m["id"] == id)
}

pub fn llm_model(id: &str) -> Option<&'static Value> {
    CONTRACT["llm_models"]
        .as_array()?
        .iter()
        .find(|m| m["id"] == id)
}
