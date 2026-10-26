//! Runs the generated fixtures in `schema/corpus/` against the core. Each file is one group;
//! a case's `input` goes in, the result must equal `expected`. `differs` and `rule` record
//! where the old releases disagreed and are ignored here.

use std::{fs, path::PathBuf};

use logquill_core::{build_llm_block, format_timestamp, parse_record, Level};
use serde_json::{json, Map, Value};

struct Case {
    name: String,
    input: Value,
    expected: Value,
}

fn load(group: &str) -> Vec<Case> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../schema/corpus")
        .join(format!("{group}.json"));
    let text = fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));
    let file: Value = serde_json::from_str(&text).unwrap_or_else(|e| panic!("{group}: {e}"));
    assert_eq!(
        file["group"], group,
        "{group}: the file names another group"
    );
    let cases = file["cases"].as_array().expect("cases is a list");
    assert!(!cases.is_empty(), "{group}: no cases");
    cases
        .iter()
        .map(|case| Case {
            name: case["name"].as_str().expect("a case name").to_string(),
            input: case["input"].clone(),
            expected: case["expected"].clone(),
        })
        .collect()
}

#[test]
fn levels() {
    for case in load("levels") {
        let got = match Level::from_json(&case.input["value"]) {
            Ok(level) => json!({ "level": level.name() }),
            Err(error) => json!({ "error": error.to_string() }),
        };
        assert_eq!(got, case.expected, "{}", case.name);
    }
}

#[test]
fn timestamps() {
    for case in load("records") {
        if case.input["op"] != "timestamp" {
            continue;
        }
        let micros = case.input["unix_micros"].as_i64().expect("microseconds");
        let got = json!({ "timestamp": format_timestamp(micros) });
        assert_eq!(got, case.expected, "{}", case.name);
    }
}

#[test]
fn llm_blocks() {
    for case in load("records") {
        if case.input["op"] != "llm_block" {
            continue;
        }
        let fields: &Map<String, Value> = case.input["fields"].as_object().expect("fields");
        let built = build_llm_block(fields);
        let got = json!({
            "block": built.block.map_or(Value::Null, Value::Object),
            "warnings": built.warnings,
        });
        assert_eq!(got, case.expected, "{}", case.name);
    }
}

#[test]
fn parsed_records() {
    for case in load("records") {
        if case.input["op"] != "parse_record" {
            continue;
        }
        let got = match parse_record(&case.input["record"]) {
            Ok(record) => json!({ "record": record.to_value() }),
            Err(error) => json!({ "error": error.to_string() }),
        };
        assert_eq!(got, case.expected, "{}", case.name);
    }
}

#[test]
fn every_group_file_is_well_formed() {
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../schema/corpus");
    let mut groups = Vec::new();
    for entry in fs::read_dir(&dir).expect("the corpus directory") {
        let path = entry.expect("a directory entry").path();
        if path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        let group = path
            .file_stem()
            .and_then(|s| s.to_str())
            .expect("a file name");
        let cases = load(group);
        let mut seen = std::collections::HashSet::new();
        for case in &cases {
            assert!(
                seen.insert(case.name.clone()),
                "{group}: duplicate case {}",
                case.name
            );
            assert!(
                !case.expected.is_null(),
                "{group}: {} has no expected output",
                case.name
            );
            assert!(!case.input.is_null(), "{group}: {} has no input", case.name);
        }
        groups.push(group.to_string());
    }
    groups.sort();
    assert!(
        groups.len() >= 22,
        "expected the full set of groups, found {groups:?}"
    );
}
