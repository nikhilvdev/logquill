//! Runs the integrity groups of the shared corpus (`schema/corpus/`) and the golden chain
//! against canonical JSON, the content policy, field classes and the hash chain.

use std::{fs, path::PathBuf};

use logquill_core::{
    apply_content_policy, keys_in_class, sha256_hex, verify_chain, verify_signed_chain, Canon,
    Chain, ContentPolicy, FieldClass, Verification, CONTENT_FIELDS, FIELD_CLASSES, GENESIS_HASH,
};
use serde_json::{json, Map, Value};

struct Case {
    name: String,
    input: Value,
    expected: Value,
}

fn schema_path(relative: &str) -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../schema")
        .join(relative)
}

fn load(group: &str) -> Vec<Case> {
    let text =
        fs::read_to_string(schema_path(&format!("corpus/{group}.json"))).expect("a corpus file");
    let file: Value = serde_json::from_str(&text).expect("valid JSON");
    file["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .map(|case| Case {
            name: case["name"].as_str().expect("a name").to_string(),
            input: case["input"].clone(),
            expected: case["expected"].clone(),
        })
        .collect()
}

#[test]
fn canonical_json() {
    for case in load("canonical-json") {
        let text = Canon::parse(case.input["json"].as_str().expect("json text"))
            .unwrap_or_else(|e| panic!("{}: {e}", case.name))
            .to_text();
        let got = json!({ "text": text, "sha256": sha256_hex(&text) });
        assert_eq!(got, case.expected, "{}", case.name);
    }
}

#[test]
fn content_policy() {
    for case in load("content-policy") {
        let got = if let Some(bad) = case.input.get("error_policy") {
            match ContentPolicy::parse(bad.as_str().expect("a name")) {
                Ok(_) => json!({ "ok": true }),
                Err(error) => json!({ "error": error.to_string() }),
            }
        } else {
            let policy = ContentPolicy::parse(case.input["policy"].as_str().expect("policy"))
                .expect("a valid policy");
            let field = case
                .input
                .get("field")
                .and_then(Value::as_str)
                .unwrap_or("input_messages");
            let value: Value = serde_json::from_str(case.input["json"].as_str().expect("json"))
                .unwrap_or_else(|e| panic!("{}: {e}", case.name));
            let mut meta = Map::new();
            meta.insert(field.to_string(), value);
            apply_content_policy(&mut meta, policy);
            json!({ "value": meta[field] })
        };
        assert_eq!(got, case.expected, "{}", case.name);
    }
}

#[test]
fn field_classes() {
    for case in load("field-classes") {
        let registry: Map<String, Value> = FIELD_CLASSES
            .iter()
            .map(|(k, c)| (k.to_string(), json!(c.name())))
            .collect();
        let got = json!({
            "registry": registry,
            "content_fields": CONTENT_FIELDS,
            "keys_in_class": {
                "secret": keys_in_class(FieldClass::Secret),
                "pii": keys_in_class(FieldClass::Pii),
                "content": keys_in_class(FieldClass::Content),
            },
        });
        assert_eq!(got, case.expected, "{}", case.name);
    }
}

fn mutate(chain: &[Value], steps: &[Value]) -> Vec<Value> {
    let mut out = chain.to_vec();
    for step in steps {
        let at = step["index"].as_u64().unwrap_or(0) as usize;
        let value = step["value"].clone();
        match step["type"].as_str().expect("a mutation type") {
            "edit_message" => out[at]["message"] = value,
            "edit_level" => out[at]["level"] = value,
            "edit_schema_version" => out[at]["schema_version"] = value,
            "edit_meta" => out[at]["meta"][step["key"].as_str().unwrap()] = value,
            "edit_llm" => out[at]["llm"][step["key"].as_str().unwrap()] = value,
            "edit_hash" => out[at]["meta"]["hash"] = value,
            "edit_prev_hash" => out[at]["meta"]["prev_hash"] = value,
            "drop_hash" => {
                out[at]["meta"]
                    .as_object_mut()
                    .unwrap()
                    .shift_remove("hash");
            }
            "delete" => {
                out.remove(at);
            }
            "swap" => out.swap(at, step["other"].as_u64().unwrap() as usize),
            "truncate" => out.truncate(step["keep"].as_u64().unwrap() as usize),
            other => panic!("unknown mutation {other}"),
        }
    }
    out
}

fn verification_json(v: &Verification) -> Value {
    json!({
        "ok": v.ok,
        "records_checked": v.records_checked,
        "broken_at": v.broken_at,
        "reason": v.reason,
        "head_hash": v.head_hash,
    })
}

#[test]
fn hash_chains() {
    for case in load("hash-chain") {
        let genesis = case
            .input
            .get("genesis")
            .and_then(Value::as_str)
            .unwrap_or(GENESIS_HASH);
        let mut chain = Chain::new(genesis);
        let stamped: Vec<Value> = case.input["records"]
            .as_array()
            .expect("records")
            .iter()
            .map(|record| {
                let mut record = record.clone();
                chain.stamp(&mut record);
                record
            })
            .collect();
        let mut got = json!({ "chain": stamped, "head_hash": chain.head() });
        let key = case.input.get("key").and_then(Value::as_str);
        let signature = key.map(|k| chain.sign_head(k.as_bytes()));
        if let Some(signature) = &signature {
            got["signature"] = json!(signature);
        }
        let steps = case
            .input
            .get("mutations")
            .and_then(Value::as_array)
            .cloned()
            .unwrap_or_default();
        let mutated = mutate(&stamped, &steps);
        got["verify"] = verification_json(&verify_chain(&mutated, genesis));
        if let (Some(key), Some(signature)) = (key, &signature) {
            got["verify_signed"] = verification_json(&verify_signed_chain(
                &mutated,
                key.as_bytes(),
                signature,
                genesis,
            ));
        }
        assert_eq!(got, case.expected, "{}", case.name);
    }
}

#[test]
fn the_golden_chain_verifies() {
    let text = fs::read_to_string(schema_path("golden_chain.json")).expect("the golden chain");
    let records: Value = serde_json::from_str(&text).expect("valid JSON");
    let records = records.as_array().expect("a list of records");
    let result = verify_chain(records, GENESIS_HASH);
    assert!(result.ok, "{:?}", result.reason);
    assert_eq!(result.records_checked, records.len());
}
