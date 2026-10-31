//! The core's plugin logic for the browser: redaction, PII scanning, the tamper chain, content policy
//! and the row builders (ECS, SQL, graph). Mirrors `bindings/node/src/plugins.rs`; records and `meta`
//! cross as JSON text. Structured results come back as JSON text for `js/index.mjs` to parse.

use std::sync::Arc;

use logquill_core as core;
use serde_json::{json, Map, Value};
use wasm_bindgen::prelude::*;

use crate::{fail, object_of, parse, verification_json, Options};

fn meta_of(text: &str) -> Result<Map<String, Value>, JsError> {
    object_of(text, "the meta")
}

fn field_class(name: &str) -> Result<core::FieldClass, JsError> {
    match name {
        "secret" => Ok(core::FieldClass::Secret),
        "pii" => Ok(core::FieldClass::Pii),
        "content" => Ok(core::FieldClass::Content),
        other => Err(fail(format!(
            "a field class is 'secret', 'pii' or 'content', got '{other}'"
        ))),
    }
}

/// `record` (JSON text) with Elastic Common Schema fields added alongside its own.
#[wasm_bindgen(js_name = toEcsDocument)]
pub fn to_ecs_document(record: &str) -> Result<String, JsError> {
    Ok(core::to_ecs_document(&parse(record, "the record")?).to_string())
}

/// The columns of one SQL log row, in table order, as a JSON array (`llm` is `null` without an LLM block).
#[wasm_bindgen(js_name = sqlRow)]
pub fn sql_row(record: &str) -> Result<String, JsError> {
    Ok(core::sql_row(&parse(record, "the record")?).to_string())
}

/// `CREATE TABLE IF NOT EXISTS` for the log table in `dialect` (`sqlite`, `postgres` or `mysql`).
#[wasm_bindgen(js_name = createTableSql)]
pub fn create_table_sql(dialect: &str, table: &str) -> Result<String, JsError> {
    let dialect = match dialect {
        "sqlite" => core::Dialect::Sqlite,
        "postgres" => core::Dialect::Postgres,
        "mysql" => core::Dialect::Mysql,
        other => {
            return Err(fail(format!(
                "dialect must be 'sqlite', 'postgres' or 'mysql', got '{other}'"
            )))
        }
    };
    Ok(core::create_table_sql(dialect, table))
}

/// One record as a graph node: its key, properties, session and parent (JSON text).
#[wasm_bindgen(js_name = graphRow)]
pub fn graph_row(
    record: &str,
    run_key: Option<String>,
    trace_key: Option<String>,
) -> Result<String, JsError> {
    let keys = core::GraphKeys {
        run_key: run_key.unwrap_or_else(|| "run_id".to_string()),
        trace_key: trace_key.unwrap_or_else(|| "trace_id".to_string()),
    };
    Ok(core::graph_row(&parse(record, "the record")?, &keys).to_string())
}

/// The well-known `meta` keys and their class, in registry order: JSON text of `[{key, class}, ...]`.
#[wasm_bindgen(js_name = fieldClassesJson)]
pub fn field_classes_json() -> String {
    Value::Array(
        core::FIELD_CLASSES
            .iter()
            .map(|(key, class)| json!({"key": key, "class": class.name()}))
            .collect(),
    )
    .to_string()
}

/// `value` (JSON text) as the content-capture policy (`off`, `hash`, `truncate` or `full`) leaves it.
#[wasm_bindgen(js_name = applyPolicyValue)]
pub fn apply_policy_value(value: &str, policy: &str) -> Result<String, JsError> {
    let policy = core::ContentPolicy::parse(policy).map_err(|e| fail(e.to_string()))?;
    Ok(policy.apply(&parse(value, "the value")?).to_string())
}

/// `meta` (JSON text) with its content fields replaced as the capture policy requires.
#[wasm_bindgen(js_name = applyContentPolicy)]
pub fn apply_content_policy(meta: &str, policy: &str) -> Result<String, JsError> {
    let policy = core::ContentPolicy::parse(policy).map_err(|e| fail(e.to_string()))?;
    let mut map = meta_of(meta)?;
    core::apply_content_policy(&mut map, policy);
    Ok(Value::Object(map).to_string())
}

/// The `meta` keys a content-capture policy governs.
#[wasm_bindgen(js_name = contentFields)]
pub fn content_fields() -> Vec<String> {
    core::CONTENT_FIELDS
        .iter()
        .map(|k| (*k).to_string())
        .collect()
}

/// The well-known keys of a field class (`secret`, `pii` or `content`).
#[wasm_bindgen(js_name = keysInClass)]
pub fn keys_in_class(name: &str) -> Result<Vec<String>, JsError> {
    Ok(core::keys_in_class(field_class(name)?)
        .into_iter()
        .map(str::to_string)
        .collect())
}

/// The key `graphRow` gives a record that has no `span_id` of its own.
#[wasm_bindgen(js_name = leafPlaceholder)]
pub fn leaf_placeholder() -> String {
    core::LEAF_PLACEHOLDER.to_string()
}

/// The Cypher statement every graph transport runs for a batch of rows.
#[wasm_bindgen(js_name = cypher)]
pub fn cypher() -> String {
    core::CYPHER.to_string()
}

/// Redacts `meta` values by key, case-insensitively, at every depth.
#[wasm_bindgen(js_name = KeyRedactor)]
pub struct KeyRedactor {
    inner: core::KeyRedactor,
}

#[wasm_bindgen(js_class = KeyRedactor)]
impl KeyRedactor {
    /// `options` is JSON text of `{keys?, classes?, replacement?}`.
    #[wasm_bindgen(constructor)]
    pub fn new(options: Option<String>) -> Result<KeyRedactor, JsError> {
        let o = Options::from_json(options)?;
        let keys = o.strings("keys").unwrap_or_else(|| {
            core::DEFAULT_KEYS
                .iter()
                .map(|k| (*k).to_string())
                .collect()
        });
        let classes = o
            .strings("classes")
            .unwrap_or_default()
            .iter()
            .map(|name| field_class(name))
            .collect::<Result<Vec<_>, _>>()?;
        let replacement = o.string("replacement").unwrap_or_else(|| "***".to_string());
        Ok(KeyRedactor {
            inner: core::KeyRedactor::new(keys.iter().map(String::as_str), &classes, &replacement),
        })
    }

    /// A copy of `meta` (JSON text) with the matching values replaced.
    #[wasm_bindgen(js_name = redactMeta)]
    pub fn redact_meta(&self, meta: &str) -> Result<String, JsError> {
        let mut map = meta_of(meta)?;
        self.inner.redact_meta(&mut map);
        Ok(Value::Object(map).to_string())
    }

    /// The text to show for a local variable: the replacement if its name matches.
    #[wasm_bindgen(js_name = redactLocal)]
    pub fn redact_local(&self, name: &str, text: &str) -> String {
        self.inner.redact_local(name, text)
    }
}

/// Redacts PII- and secret-shaped text anywhere in `meta`.
#[wasm_bindgen(js_name = PiiRedactor)]
pub struct PiiRedactor {
    inner: core::PiiRedactor,
}

#[wasm_bindgen(js_class = PiiRedactor)]
impl PiiRedactor {
    #[wasm_bindgen(constructor)]
    pub fn new(replacement: Option<String>) -> PiiRedactor {
        PiiRedactor {
            inner: core::PiiRedactor::new(&replacement.unwrap_or_else(|| "***".to_string())),
        }
    }

    /// A copy of `meta` (JSON text) with matches in every string, at every depth, replaced.
    #[wasm_bindgen(js_name = redactMeta)]
    pub fn redact_meta(&self, meta: &str) -> Result<String, JsError> {
        let mut map = meta_of(meta)?;
        self.inner.redact_meta(&mut map);
        Ok(Value::Object(map).to_string())
    }

    /// `text` with matches replaced.
    #[wasm_bindgen(js_name = redactText)]
    pub fn redact_text(&self, text: &str) -> String {
        self.inner.redact_text(text)
    }
}

/// A hash chain that several writers share.
#[wasm_bindgen(js_name = Chain)]
pub struct Chain {
    inner: Arc<core::SharedChain>,
}

#[wasm_bindgen(js_class = Chain)]
impl Chain {
    #[wasm_bindgen(constructor)]
    pub fn new(genesis: Option<String>) -> Chain {
        let chain = genesis
            .as_deref()
            .map_or_else(core::Chain::default, core::Chain::new);
        Chain {
            inner: Arc::new(core::SharedChain::new(chain)),
        }
    }

    /// The hash of the last record chained (the genesis hash before any).
    pub fn head(&self) -> String {
        self.inner.head()
    }

    /// HMAC-SHA256 of the head under `key`, hex-encoded.
    #[wasm_bindgen(js_name = signHead)]
    pub fn sign_head(&self, key: &[u8]) -> String {
        core::sign_head(&self.inner.head(), key)
    }

    /// Chains `record` (JSON text) now and returns it with `meta.prev_hash` and `meta.hash` set.
    pub fn stamp(&self, record: &str) -> Result<String, JsError> {
        let value = parse(record, "the record")?;
        Ok(self.inner.write(value, Value::clone).to_string())
    }
}

/// HMAC-SHA256 of `head_hash` under `key`, hex-encoded.
#[wasm_bindgen(js_name = signHead)]
pub fn sign_head(head_hash: &str, key: &[u8]) -> String {
    core::sign_head(head_hash, key)
}

/// Whether `signature` is what `signHead(headHash, key)` gives (compared in constant time).
#[wasm_bindgen(js_name = verifyHeadSignature)]
pub fn verify_head_signature(head_hash: &str, key: &[u8], signature: &str) -> bool {
    core::verify_head_signature(head_hash, key, signature)
}

/// Verifies a chain (JSON text of an array) and that its head matches `signature`. The result is JSON text
/// shaped like `verifyChainJson`'s.
#[wasm_bindgen(js_name = verifySignedChainJson)]
pub fn verify_signed_chain_json(
    records: &str,
    key: &[u8],
    signature: &str,
    genesis: Option<String>,
) -> Result<String, JsError> {
    let Value::Array(items) = parse(records, "the records")? else {
        return Err(fail("the records must be a JSON array of record objects"));
    };
    Ok(verification_json(core::verify_signed_chain(
        items.iter(),
        key,
        signature,
        genesis.as_deref().unwrap_or(core::GENESIS_HASH),
    )))
}
