//! Keeps the shared golden fixtures wired into `cargo test`. The real conformance checks
//! (parse, validate, round-trip) arrive with the record parser; this proves the files are
//! found and well formed from the first commit.

use std::{fs, path::PathBuf};

fn golden_records() -> serde_json::Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../schema/golden_records.json");
    let text = fs::read_to_string(&path).expect("schema/golden_records.json is readable");
    serde_json::from_str(&text).expect("schema/golden_records.json is valid JSON")
}

#[test]
fn golden_records_have_all_four_groups() {
    let golden = golden_records();
    for group in ["valid", "invalid", "legacy", "rejected"] {
        let cases = golden[group].as_array();
        assert!(
            cases.is_some_and(|cases| !cases.is_empty()),
            "group {group} is missing or empty"
        );
    }
}
