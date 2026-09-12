use anyhow::Result;
use serde_json::Value;
use vocal_more_backend::billing;

#[test]
fn shipped_price_snapshot_and_usage_schemas_match_python() -> Result<()> {
    let cases: Value = serde_json::from_str(include_str!("fixtures/billing.json"))?;
    for case in cases.as_array().unwrap() {
        let result = if case["stage"] == "asr" {
            billing::asr(
                case["model"].as_str().unwrap(),
                case["seconds"].as_f64().unwrap(),
                &case["usage"],
            )
        } else {
            billing::polish(
                case["model"].as_str().unwrap(),
                case["thinking"].as_bool().unwrap(),
                &case["usage"],
            )
        };
        assert_eq!(result, case["expected"], "{case}");
    }
    Ok(())
}
