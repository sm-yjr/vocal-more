// SPDX-License-Identifier: GPL-3.0-only
//! Native capture preserves the macOS virtual key and left/right modifier identity.
use serde_json::{Value, json};

#[derive(Default)]
pub struct Capture {
    pub active: bool,
    pub pending: Option<Value>,
}
pub enum Outcome {
    Waiting,
    Cancel,
    Commit(Value),
    Ignored,
}
impl Capture {
    pub fn receive(&mut self, event: &Value) -> Outcome {
        if !self.active || event["repeat"] == true {
            return Outcome::Ignored;
        }
        let Some(code) = event["key_code"].as_u64() else {
            return Outcome::Ignored;
        };
        if code == 53 {
            self.active = false;
            self.pending = None;
            return Outcome::Cancel;
        }
        let key = json!({"key_code":code,"display_name":event["display_name"].as_str().unwrap_or("Key"),"is_modifier":event["is_modifier"]==true,"flag_mask":event["flag_mask"].as_u64().unwrap_or(0)});
        if key["is_modifier"] == true
            && self
                .pending
                .as_ref()
                .is_none_or(|pending| pending["key_code"] != code)
        {
            self.pending = Some(key);
            return Outcome::Waiting;
        }
        self.active = false;
        self.pending = None;
        Outcome::Commit(key)
    }
}
pub fn custom_keys(config: &Value) -> Vec<Value> {
    if let Some(keys) = config["hotkey"]["custom_keys"].as_array() {
        return keys.clone();
    }
    config["hotkey"]["custom_key"]
        .as_object()
        .map(|_| vec![config["hotkey"]["custom_key"].clone()])
        .unwrap_or_default()
}
pub fn append(keys: &[Value], next: Value) -> Option<Value> {
    if keys.len() >= 8 || keys.iter().any(|key| key["key_code"] == next["key_code"]) {
        return None;
    }
    let mut keys = keys.to_vec();
    keys.push(next);
    Some(json!(keys))
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn modifier_requires_distinct_second_press_and_preserves_side() {
        let mut capture = Capture {
            active: true,
            pending: None,
        };
        let left = json!({"key_code":55,"display_name":"Left Command","is_modifier":true,"flag_mask":1048576});
        assert!(matches!(capture.receive(&left), Outcome::Waiting));
        let mut repeated = left.clone();
        repeated["repeat"] = json!(true);
        assert!(matches!(capture.receive(&repeated), Outcome::Ignored));
        assert!(matches!(capture.receive(&left),Outcome::Commit(value) if value["key_code"]==55));
    }
    #[test]
    fn escape_cancels_and_duplicates_do_not_grow() {
        let mut capture = Capture {
            active: true,
            pending: None,
        };
        assert!(matches!(
            capture.receive(&json!({"key_code":53})),
            Outcome::Cancel
        ));
        let key = json!({"key_code":0});
        assert!(append(std::slice::from_ref(&key), key.clone()).is_none());
    }
}
