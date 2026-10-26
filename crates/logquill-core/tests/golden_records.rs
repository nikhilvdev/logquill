//! Runs the shared golden records (`schema/golden_records.json`) against the record parser and
//! the JSON Schema, the way every language's test suite must.

use std::{fs, path::PathBuf};

use logquill_core::parse_record;
use serde_json::Value;

fn schema_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../schema")
}

fn load(name: &str) -> Value {
    let path = schema_dir().join(name);
    let text = fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
}

fn cases(golden: &Value, group: &str) -> Vec<Value> {
    let cases = golden[group]
        .as_array()
        .unwrap_or_else(|| panic!("group {group} is missing"));
    assert!(!cases.is_empty(), "group {group} is empty");
    cases.clone()
}

fn name(case: &Value) -> &str {
    case["name"].as_str().unwrap_or("(unnamed)")
}

#[test]
fn valid_records_validate_parse_and_round_trip() {
    let validator = jsonschema::validator_for(&load("record.schema.json")).expect("a valid schema");
    for case in cases(&load("golden_records.json"), "valid") {
        let record = &case["record"];
        assert!(
            validator.is_valid(record),
            "{} should validate",
            name(&case)
        );

        let read = parse_record(record).unwrap_or_else(|e| panic!("{}: {e}", name(&case)));
        assert_eq!(
            &read.to_value(),
            record,
            "{} should read back unchanged",
            name(&case)
        );

        let text = serde_json::to_string(record).expect("serialises");
        let again: Value = serde_json::from_str(&text).expect("parses");
        assert_eq!(
            &again,
            record,
            "{} should survive a JSON round trip",
            name(&case)
        );
    }
}

#[test]
fn invalid_records_do_not_validate() {
    let validator = jsonschema::validator_for(&load("record.schema.json")).expect("a valid schema");
    for case in cases(&load("golden_records.json"), "invalid") {
        assert!(
            !validator.is_valid(&case["record"]),
            "{} should not validate",
            name(&case)
        );
    }
}

#[test]
fn legacy_records_read_as_the_expected_current_shape() {
    for case in cases(&load("golden_records.json"), "legacy") {
        let read = parse_record(&case["record"]).unwrap_or_else(|e| panic!("{}: {e}", name(&case)));
        assert_eq!(read.to_value(), case["parsed"], "{}", name(&case));
    }
}

#[test]
fn rejected_records_are_refused() {
    for case in cases(&load("golden_records.json"), "rejected") {
        assert!(
            parse_record(&case["record"]).is_err(),
            "{} should be refused",
            name(&case)
        );
    }
}
