//! The tamper-evident hash chain, head signing and verification
//! (`docs/spec/03-privacy-tracing-otel.md`, "Hash chain").

use std::borrow::Borrow;

use hmac::{Hmac, Mac};
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

use crate::canonical::Canon;
use crate::LEGACY_SCHEMA_VERSION;

type HmacSha256 = Hmac<Sha256>;

/// The `prev_hash` of the first record in a chain.
pub const GENESIS_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

pub(crate) fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// Lower-case SHA-256 of `text`.
pub fn sha256_hex(text: &str) -> String {
    hex(&Sha256::digest(text.as_bytes()))
}

/// The hash a record gets when it follows `prev_hash`: SHA-256 of `prev_hash` followed by the
/// canonical text of its timestamp, level, logger, message, `meta` without `hash` and `prev_hash`,
/// and (when present and not `"1.0"`) `schema_version` and (when present) `llm`.
pub fn hash_record(record: &Value, prev_hash: &str) -> String {
    let field = |name: &str| record.get(name).map_or(Canon::Null, Canon::from_value);
    let meta: Vec<(String, Canon)> = record
        .get("meta")
        .and_then(Value::as_object)
        .map(|meta| {
            meta.iter()
                .filter(|(k, _)| k.as_str() != "hash" && k.as_str() != "prev_hash")
                .map(|(k, v)| (k.clone(), Canon::from_value(v)))
                .collect()
        })
        .unwrap_or_default();
    let mut content = vec![
        ("timestamp".to_string(), field("timestamp")),
        ("level".to_string(), field("level")),
        ("logger".to_string(), field("logger")),
        ("message".to_string(), field("message")),
        ("meta".to_string(), Canon::Obj(meta)),
    ];
    if let Some(version) = record.get("schema_version") {
        if version.as_str() != Some(LEGACY_SCHEMA_VERSION) {
            content.push(("schema_version".to_string(), Canon::from_value(version)));
        }
    }
    if let Some(llm) = record.get("llm") {
        content.push(("llm".to_string(), Canon::from_value(llm)));
    }
    sha256_hex(&format!("{prev_hash}{}", Canon::Obj(content).to_text()))
}

/// Chains records one after another: stamps `meta.prev_hash` and `meta.hash`.
#[derive(Clone, Debug)]
pub struct Chain {
    last: String,
}

impl Default for Chain {
    fn default() -> Self {
        Chain::new(GENESIS_HASH)
    }
}

impl Chain {
    /// A chain that continues from `genesis` (for example across a log rotation).
    pub fn new(genesis: &str) -> Chain {
        Chain {
            last: genesis.to_string(),
        }
    }

    /// Adds `prev_hash` and `hash` to the record's `meta` (created if missing) and returns the hash.
    pub fn stamp(&mut self, record: &mut Value) -> String {
        let digest = hash_record(record, &self.last);
        if let Some(object) = record.as_object_mut() {
            let meta = object
                .entry("meta")
                .or_insert_with(|| Value::Object(Map::new()));
            if !meta.is_object() {
                *meta = Value::Object(Map::new());
            }
            if let Some(meta) = meta.as_object_mut() {
                meta.insert("prev_hash".into(), Value::String(self.last.clone()));
                meta.insert("hash".into(), Value::String(digest.clone()));
            }
        }
        self.last = digest.clone();
        digest
    }

    /// The hash of the last record stamped, or the genesis hash if none was.
    pub fn head(&self) -> &str {
        &self.last
    }

    /// `sign_head(self.head(), key)`.
    pub fn sign_head(&self, key: &[u8]) -> String {
        sign_head(&self.last, key)
    }
}

/// What verification found. `broken_at` is where corruption was *detected* (1-based), which is
/// not necessarily where it was introduced: a deleted line shows at the record after the gap.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Verification {
    pub ok: bool,
    /// Records looked at, including the one the chain broke on.
    pub records_checked: usize,
    pub broken_at: Option<usize>,
    pub reason: Option<String>,
    /// The chain head as of the last record verified (`None` for a failure on the first record).
    pub head_hash: Option<String>,
}

const NO_HASH: &str =
    "this record has no meta.hash — it was never chained, or that field was removed";
const BAD_PREV: &str = "meta.prev_hash doesn't match the previous record's hash — a line before this one was edited, removed, or reordered";
const BAD_CONTENT: &str = "this record's content doesn't match its own stored hash — this line was edited after being chained";
const BAD_SIGNATURE: &str = "the chain is internally consistent, but its head doesn't match the given signature — the file may have been truncated, or replaced outright, after the signature was taken";

fn failure(checked: usize, reason: &str, head: Option<String>) -> Verification {
    Verification {
        ok: false,
        records_checked: checked,
        broken_at: Some(checked),
        reason: Some(reason.to_string()),
        head_hash: head,
    }
}

/// Checks that every record's hash matches its content plus the previous record's hash, in order.
pub fn verify_chain<R: Borrow<Value>>(
    records: impl IntoIterator<Item = R>,
    genesis: &str,
) -> Verification {
    let mut prev = genesis.to_string();
    let mut checked = 0;
    for record in records {
        let record = record.borrow();
        checked += 1;
        let meta = record.get("meta");
        let stored = meta.and_then(|m| m.get("hash")).filter(|v| !v.is_null());
        let Some(stored) = stored else {
            return failure(checked, NO_HASH, (checked > 1).then(|| prev.clone()));
        };
        if meta
            .and_then(|m| m.get("prev_hash"))
            .and_then(Value::as_str)
            != Some(prev.as_str())
        {
            return failure(checked, BAD_PREV, Some(prev));
        }
        if stored.as_str() != Some(hash_record(record, &prev).as_str()) {
            return failure(checked, BAD_CONTENT, Some(prev));
        }
        prev = stored.as_str().unwrap_or_default().to_string();
    }
    Verification {
        ok: true,
        records_checked: checked,
        broken_at: None,
        reason: None,
        head_hash: Some(if checked > 0 {
            prev
        } else {
            genesis.to_string()
        }),
    }
}

fn mac_for(key: &[u8]) -> HmacSha256 {
    // HMAC accepts a key of any length, so the fallback is never used; it keeps the core free of panics.
    HmacSha256::new_from_slice(key).unwrap_or_else(|_| HmacSha256::new(&Default::default()))
}

/// Lower-case hex HMAC-SHA256 of `head_hash` under `key`. Keep the result outside the log file:
/// a signature next to the data it vouches for protects nothing.
pub fn sign_head(head_hash: &str, key: &[u8]) -> String {
    let mut mac = mac_for(key);
    mac.update(head_hash.as_bytes());
    hex(&mac.finalize().into_bytes())
}

fn unhex(text: &str) -> Option<Vec<u8>> {
    if text.len() % 2 != 0 || !text.is_ascii() {
        return None;
    }
    (0..text.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&text[i..i + 2], 16).ok())
        .collect()
}

/// Whether `signature` is what [`sign_head`] gives, compared in constant time.
pub fn verify_head_signature(head_hash: &str, key: &[u8], signature: &str) -> bool {
    let Some(given) = unhex(signature) else {
        return false;
    };
    let mut mac = mac_for(key);
    mac.update(head_hash.as_bytes());
    mac.verify_slice(&given).is_ok()
}

/// [`verify_chain`], then confirm the head against an independently held `signature`.
pub fn verify_signed_chain<R: Borrow<Value>>(
    records: impl IntoIterator<Item = R>,
    key: &[u8],
    signature: &str,
    genesis: &str,
) -> Verification {
    let result = verify_chain(records, genesis);
    if !result.ok {
        return result;
    }
    match &result.head_hash {
        Some(head) if verify_head_signature(head, key, signature) => result,
        _ => failure(result.records_checked, BAD_SIGNATURE, result.head_hash),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn record(n: u32) -> Value {
        json!({"timestamp": "t", "level": "INFO", "logger": "l", "message": format!("m{n}"), "meta": {"n": n}})
    }

    #[test]
    fn a_stamped_chain_verifies_and_an_edit_is_caught() {
        let mut chain = Chain::default();
        let mut records: Vec<Value> = (0..3).map(record).collect();
        for r in &mut records {
            chain.stamp(r);
        }
        assert!(verify_chain(&records, GENESIS_HASH).ok);
        records[1]["message"] = json!("tampered");
        let result = verify_chain(&records, GENESIS_HASH);
        assert_eq!((result.ok, result.broken_at), (false, Some(2)));
    }

    #[test]
    fn meta_order_does_not_change_the_hash_but_the_output_keeps_it() {
        let a = json!({"timestamp": "t", "level": "INFO", "logger": "l", "message": "m", "meta": {"b": 1, "a": 2}});
        let b = json!({"timestamp": "t", "level": "INFO", "logger": "l", "message": "m", "meta": {"a": 2, "b": 1}});
        assert_eq!(hash_record(&a, GENESIS_HASH), hash_record(&b, GENESIS_HASH));
        let keys: Vec<_> = a["meta"].as_object().unwrap().keys().collect();
        assert_eq!(keys, ["b", "a"]);
    }

    #[test]
    fn signatures_are_checked_in_hex_and_reject_garbage() {
        let signature = sign_head("abc", b"key");
        assert!(verify_head_signature("abc", b"key", &signature));
        assert!(!verify_head_signature("abd", b"key", &signature));
        assert!(!verify_head_signature("abc", b"key", "zz"));
        assert!(!verify_head_signature("abc", b"key", "é"));
    }
}
