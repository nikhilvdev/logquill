//! The first-class `llm` block of an LLM-call record (`docs/spec/01-records-and-logger.md`).

use serde_json::{Map, Value};

const KNOWN_FIELDS: &str = "model, tokens_in, tokens_out, cost_usd, latency_ms, finish_reason";

/// What [`build_llm_block`] produced.
#[derive(Clone, Debug, PartialEq)]
pub struct LlmBlock {
    /// The validated block, or `None` when nothing usable was left.
    pub block: Option<Map<String, Value>>,
    /// One message per field that was dropped. The core has no logger; the caller decides
    /// where these go (a log call must never fail over a bad value).
    pub warnings: Vec<String>,
}

/// What a field must be, or `None` for a name the contract does not define.
fn expected(name: &str) -> Option<&'static str> {
    match name {
        "model" | "finish_reason" => Some("a string"),
        "tokens_in" | "tokens_out" => Some("a non-negative integer"),
        "cost_usd" | "latency_ms" => Some("a non-negative number"),
        _ => None,
    }
}

fn is_valid(name: &str, value: &Value) -> bool {
    match (name, value) {
        ("model" | "finish_reason", Value::String(_)) => true,
        ("tokens_in" | "tokens_out", Value::Number(number)) => number.as_u64().is_some(),
        ("cost_usd" | "latency_ms", Value::Number(number)) => {
            number.as_f64().is_some_and(|float| float >= 0.0)
        }
        _ => false,
    }
}

/// Builds an `llm` block from named fields: a `null` field is skipped, a field of the wrong
/// type (a negative count, a string where a number belongs, a boolean) or an unknown name is
/// dropped with a warning, and if nothing is left there is no block at all.
pub fn build_llm_block(fields: &Map<String, Value>) -> LlmBlock {
    let mut block = Map::new();
    let mut warnings = Vec::new();
    for (name, value) in fields {
        if value.is_null() {
            continue;
        }
        if is_valid(name, value) {
            block.insert(name.clone(), value.clone());
        } else {
            let wanted = expected(name)
                .unwrap_or(&format!("one of {KNOWN_FIELDS}"))
                .to_string();
            warnings.push(format!(
                "llm_call: ignoring {name}={value} — expected {wanted}"
            ));
        }
    }
    LlmBlock {
        block: (!block.is_empty()).then_some(block),
        warnings,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fields(value: Value) -> Map<String, Value> {
        value.as_object().expect("an object").clone()
    }

    #[test]
    fn a_full_block_passes_through() {
        let input = json!({
            "model": "gpt-4o", "tokens_in": 10, "tokens_out": 20,
            "cost_usd": 0.5, "latency_ms": 12, "finish_reason": "stop"
        });
        let built = build_llm_block(&fields(input.clone()));
        assert_eq!(built.block.map(Value::Object), Some(input));
        assert!(built.warnings.is_empty());
    }

    #[test]
    fn zero_is_a_valid_number() {
        let built = build_llm_block(&fields(json!({"tokens_in": 0, "cost_usd": 0.0})));
        assert_eq!(built.block.map(|b| b.len()), Some(2));
    }

    #[test]
    fn null_fields_are_skipped_quietly() {
        let built = build_llm_block(&fields(json!({"model": null, "tokens_in": null})));
        assert_eq!(built.block, None);
        assert!(built.warnings.is_empty());
    }

    #[test]
    fn bad_fields_are_dropped_with_a_warning() {
        let built = build_llm_block(&fields(json!({
            "model": "m", "tokens_in": -1, "tokens_out": "7", "cost_usd": true, "latency_ms": 1.5
        })));
        let kept = built.block.expect("model and latency_ms survive");
        assert_eq!(kept.keys().collect::<Vec<_>>(), ["model", "latency_ms"]);
        assert_eq!(
            built.warnings,
            [
                "llm_call: ignoring tokens_in=-1 — expected a non-negative integer",
                "llm_call: ignoring tokens_out=\"7\" — expected a non-negative integer",
                "llm_call: ignoring cost_usd=true — expected a non-negative number",
            ]
        );
    }

    #[test]
    fn a_fractional_count_is_not_an_integer() {
        let built = build_llm_block(&fields(json!({"tokens_in": 1.5})));
        assert_eq!(built.block, None);
        assert_eq!(built.warnings.len(), 1);
    }

    #[test]
    fn an_unknown_field_lists_the_known_ones() {
        let built = build_llm_block(&fields(json!({"temperature": 0.2})));
        assert_eq!(
            built.warnings,
            [format!(
                "llm_call: ignoring temperature=0.2 — expected one of {KNOWN_FIELDS}"
            )]
        );
    }
}
