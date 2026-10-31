//! Property tests: hostile input may be refused but never panics, and the integrity rules hold
//! for any record, not just the fixtures.

use logquill_core::{
    apply_content_policy, build_llm_block, canonical_json, format_timestamp, hash_record,
    parse_record, verify_chain, Canon, Chain, ContentPolicy, Level, GENESIS_HASH,
};
use proptest::prelude::*;
use serde_json::{json, Map, Number, Value};

fn json_value() -> impl Strategy<Value = Value> {
    let leaf = prop_oneof![
        Just(Value::Null),
        any::<bool>().prop_map(Value::Bool),
        any::<i64>().prop_map(|n| json!(n)),
        any::<u64>().prop_map(|n| json!(n)),
        any::<f64>().prop_map(|x| Number::from_f64(x).map_or(Value::Null, Value::Number)),
        any::<String>().prop_map(Value::String),
    ];
    leaf.prop_recursive(4, 48, 6, |inner| {
        prop_oneof![
            prop::collection::vec(inner.clone(), 0..6).prop_map(Value::Array),
            prop::collection::vec((any::<String>(), inner), 0..6)
                .prop_map(|pairs| Value::Object(pairs.into_iter().collect::<Map<_, _>>())),
        ]
    })
}

fn meta_object() -> impl Strategy<Value = Map<String, Value>> {
    prop::collection::vec((any::<String>(), json_value()), 0..6)
        .prop_map(|pairs| pairs.into_iter().collect())
}

proptest! {
    #[test]
    fn timestamps_always_have_the_documented_shape(micros in -62_135_596_800_000_000i64..=253_402_300_799_999_999i64) {
        let text = format_timestamp(micros);
        let b = text.as_bytes();
        prop_assert_eq!(b.len(), 24, "{}", text);
        prop_assert!(text.ends_with('Z'));
        for (i, c) in text.chars().enumerate() {
            match i {
                4 | 7 => prop_assert_eq!(c, '-'),
                10 => prop_assert_eq!(c, 'T'),
                13 | 16 => prop_assert_eq!(c, ':'),
                19 => prop_assert_eq!(c, '.'),
                23 => prop_assert_eq!(c, 'Z'),
                _ => prop_assert!(c.is_ascii_digit(), "{}", text),
            }
        }
    }

    #[test]
    fn arbitrary_text_never_panics_the_canonical_parser(text in any::<String>()) {
        let _ = Canon::parse(&text);
    }

    #[test]
    fn canonical_text_is_ascii_and_stable(value in json_value()) {
        let text = canonical_json(&value);
        prop_assert!(text.is_ascii());
        let again = Canon::parse(&text).expect("canonical text is JSON").to_text();
        prop_assert_eq!(again, text);
    }

    #[test]
    fn arbitrary_values_never_panic_the_record_reader(value in json_value()) {
        let _ = parse_record(&value);
    }

    #[test]
    fn arbitrary_llm_fields_never_panic(fields in meta_object()) {
        let built = build_llm_block(&fields);
        if let Some(block) = built.block {
            prop_assert!(!block.is_empty());
        }
    }

    #[test]
    fn arbitrary_level_names_never_panic(name in any::<String>()) {
        let _ = Level::parse(&name);
    }

    #[test]
    fn the_content_policy_never_panics_and_off_hides_everything(meta in meta_object()) {
        for policy in [ContentPolicy::Off, ContentPolicy::Hash, ContentPolicy::Truncate, ContentPolicy::Full] {
            let mut copy = meta.clone();
            apply_content_policy(&mut copy, policy);
        }
        let mut hidden = meta.clone();
        hidden.insert("input_messages".into(), json!("a secret prompt"));
        apply_content_policy(&mut hidden, ContentPolicy::Off);
        prop_assert!(!Value::Object(hidden).to_string().contains("a secret prompt"));
    }

    #[test]
    fn any_chained_log_verifies_and_any_edit_breaks_it(metas in prop::collection::vec(meta_object(), 1..5), edit in any::<prop::sample::Index>()) {
        let mut chain = Chain::default();
        let records: Vec<Value> = metas.into_iter().enumerate().map(|(n, meta)| {
            let mut record = json!({
                "schema_version": "2.0", "timestamp": "t", "level": "INFO", "logger": "l",
                "message": format!("m{n}"), "meta": meta,
            });
            chain.stamp(&mut record);
            record
        }).collect();
        prop_assert!(verify_chain(&records, GENESIS_HASH).ok);

        let at = edit.index(records.len());
        let mut edited = records.clone();
        edited[at]["message"] = json!("edited");
        let result = verify_chain(&edited, GENESIS_HASH);
        prop_assert!(!result.ok);
        prop_assert_eq!(result.broken_at, Some(at + 1));
    }

    #[test]
    fn hashing_ignores_meta_order(pairs in prop::collection::vec((any::<String>(), json_value()), 0..6)) {
        let forward: Map<String, Value> = pairs.iter().cloned().collect();
        let backward: Map<String, Value> = pairs.iter().rev().cloned().collect();
        // With repeated keys the surviving value depends on order, so compare only distinct keys.
        prop_assume!(forward == backward);
        let make = |meta: Map<String, Value>| json!({"timestamp": "t", "level": "INFO", "logger": "l", "message": "m", "meta": meta});
        prop_assert_eq!(hash_record(&make(forward), GENESIS_HASH), hash_record(&make(backward), GENESIS_HASH));
    }

    #[test]
    fn verifying_arbitrary_values_never_panics(values in prop::collection::vec(json_value(), 0..5)) {
        let _ = verify_chain(&values, GENESIS_HASH);
    }
}
