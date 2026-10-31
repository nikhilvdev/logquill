//! The core's plugin logic for Node: redaction, PII scanning, the tamper chain, content policy and
//! the row builders (ECS, SQL, graph). Records and `meta` cross as JSON text, like the helpers in
//! `lib.rs`.

use std::sync::Arc;

use logquill_core as core;
use napi::bindgen_prelude::Buffer;
use napi::{Error, Result};
use napi_derive::napi;
use serde_json::{Map, Value};

use crate::{parse, verification, Verification};

fn reason(message: impl Into<String>) -> Error {
    Error::from_reason(message.into())
}

fn meta_of(text: &str) -> Result<Map<String, Value>> {
    match parse(text, "the meta")? {
        Value::Object(map) => Ok(map),
        _ => Err(reason("meta must be a JSON object")),
    }
}

/// `record` (JSON text) with Elastic Common Schema fields added alongside its own.
#[napi]
pub fn to_ecs_document(record: String) -> Result<String> {
    Ok(core::to_ecs_document(&parse(&record, "the record")?).to_string())
}

/// The columns of one SQL log row, in table order, as a JSON array (`llm` is `null` without an LLM block).
#[napi]
pub fn sql_row(record: String) -> Result<String> {
    Ok(core::sql_row(&parse(&record, "the record")?).to_string())
}

/// `CREATE TABLE IF NOT EXISTS` for the log table in `dialect` (`sqlite`, `postgres` or `mysql`).
#[napi]
pub fn create_table_sql(dialect: String, table: String) -> Result<String> {
    let dialect = match dialect.as_str() {
        "sqlite" => core::Dialect::Sqlite,
        "postgres" => core::Dialect::Postgres,
        "mysql" => core::Dialect::Mysql,
        other => {
            return Err(reason(format!(
                "dialect must be 'sqlite', 'postgres' or 'mysql', got '{other}'"
            )))
        }
    };
    Ok(core::create_table_sql(dialect, &table))
}

/// One record as a graph node: its key, properties, session and parent (JSON text).
#[napi]
pub fn graph_row(
    record: String,
    run_key: Option<String>,
    trace_key: Option<String>,
) -> Result<String> {
    let keys = core::GraphKeys {
        run_key: run_key.unwrap_or_else(|| "run_id".to_string()),
        trace_key: trace_key.unwrap_or_else(|| "trace_id".to_string()),
    };
    Ok(core::graph_row(&parse(&record, "the record")?, &keys).to_string())
}

/// A well-known `meta` key and its class.
#[napi(object)]
pub struct FieldClassEntry {
    pub key: String,
    /// `secret`, `pii` or `content`.
    pub class: String,
}

/// The well-known `meta` keys and their class, in registry order.
#[napi]
pub fn field_classes() -> Vec<FieldClassEntry> {
    core::FIELD_CLASSES
        .iter()
        .map(|(key, class)| FieldClassEntry {
            key: (*key).to_string(),
            class: class.name().to_string(),
        })
        .collect()
}

/// `value` (JSON text) as the content-capture policy (`off`, `hash`, `truncate` or `full`) leaves it.
#[napi]
pub fn apply_policy_value(value: String, policy: String) -> Result<String> {
    let policy = core::ContentPolicy::parse(&policy).map_err(|e| reason(e.to_string()))?;
    Ok(policy.apply(&parse(&value, "the value")?).to_string())
}

/// `meta` (JSON text) with its content fields replaced as the capture policy (`off`, `hash`,
/// `truncate` or `full`) requires.
#[napi]
pub fn apply_content_policy(meta: String, policy: String) -> Result<String> {
    let policy = core::ContentPolicy::parse(&policy).map_err(|e| reason(e.to_string()))?;
    let mut map = meta_of(&meta)?;
    core::apply_content_policy(&mut map, policy);
    Ok(Value::Object(map).to_string())
}

/// The `meta` keys a content-capture policy governs.
#[napi]
pub fn content_fields() -> Vec<String> {
    core::CONTENT_FIELDS
        .iter()
        .map(|k| (*k).to_string())
        .collect()
}

/// The well-known keys of a field class (`secret`, `pii` or `content`).
#[napi]
pub fn keys_in_class(name: String) -> Result<Vec<String>> {
    Ok(core::keys_in_class(field_class(&name)?)
        .into_iter()
        .map(str::to_string)
        .collect())
}

/// The key `graphRow` gives a record that has no `span_id` of its own; a transport swaps it for a
/// fresh unique one.
#[napi(js_name = "LEAF_PLACEHOLDER")]
pub const LEAF_PLACEHOLDER: &str = core::LEAF_PLACEHOLDER;

/// The Cypher statement every graph transport runs for a batch of rows.
#[napi(js_name = "CYPHER")]
pub const CYPHER: &str = core::CYPHER;

/// Redacts `meta` values by key, case-insensitively, at every depth.
#[napi]
pub struct KeyRedactor {
    inner: core::KeyRedactor,
}

/// Options for `KeyRedactor`; every field is optional.
#[napi(object)]
pub struct KeyRedactorConfig {
    /// Keys to redact, matched case-insensitively. Defaults to `password`, `token`, `secret`,
    /// `api_key` and `authorization`.
    pub keys: Option<Vec<String>>,
    /// Field classes (`secret`, `pii`, `content`) whose well-known keys are redacted as well.
    pub classes: Option<Vec<String>>,
    pub replacement: Option<String>,
}

fn field_class(name: &str) -> Result<core::FieldClass> {
    match name {
        "secret" => Ok(core::FieldClass::Secret),
        "pii" => Ok(core::FieldClass::Pii),
        "content" => Ok(core::FieldClass::Content),
        other => Err(reason(format!(
            "a field class is 'secret', 'pii' or 'content', got '{other}'"
        ))),
    }
}

#[napi]
impl KeyRedactor {
    #[napi(constructor)]
    pub fn new(config: Option<KeyRedactorConfig>) -> Result<KeyRedactor> {
        let c = config.unwrap_or(KeyRedactorConfig {
            keys: None,
            classes: None,
            replacement: None,
        });
        let keys = c.keys.unwrap_or_else(|| {
            core::DEFAULT_KEYS
                .iter()
                .map(|k| (*k).to_string())
                .collect()
        });
        let classes = c
            .classes
            .unwrap_or_default()
            .iter()
            .map(|name| field_class(name))
            .collect::<Result<Vec<_>>>()?;
        let replacement = c.replacement.unwrap_or_else(|| "***".to_string());
        Ok(KeyRedactor {
            inner: core::KeyRedactor::new(keys.iter().map(String::as_str), &classes, &replacement),
        })
    }

    /// A copy of `meta` (JSON text) with the matching values replaced.
    #[napi]
    pub fn redact_meta(&self, meta: String) -> Result<String> {
        let mut map = meta_of(&meta)?;
        self.inner.redact_meta(&mut map);
        Ok(Value::Object(map).to_string())
    }

    /// The text to show for a local variable: the replacement if its name matches.
    #[napi]
    pub fn redact_local(&self, name: String, text: String) -> String {
        self.inner.redact_local(&name, &text)
    }
}

/// Redacts PII- and secret-shaped text anywhere in `meta`.
#[napi]
pub struct PiiRedactor {
    inner: core::PiiRedactor,
}

#[napi]
impl PiiRedactor {
    #[napi(constructor)]
    pub fn new(replacement: Option<String>) -> PiiRedactor {
        PiiRedactor {
            inner: core::PiiRedactor::new(&replacement.unwrap_or_else(|| "***".to_string())),
        }
    }

    /// A copy of `meta` (JSON text) with matches in every string, at every depth, replaced.
    #[napi]
    pub fn redact_meta(&self, meta: String) -> Result<String> {
        let mut map = meta_of(&meta)?;
        self.inner.redact_meta(&mut map);
        Ok(Value::Object(map).to_string())
    }

    /// `text` with matches replaced.
    #[napi]
    pub fn redact_text(&self, text: String) -> String {
        self.inner.redact_text(&text)
    }
}

/// A hash chain that several writers share.
#[napi]
pub struct Chain {
    inner: Arc<core::SharedChain>,
}

#[napi]
impl Chain {
    #[napi(constructor)]
    pub fn new(genesis: Option<String>) -> Chain {
        let chain = genesis
            .as_deref()
            .map_or_else(core::Chain::default, core::Chain::new);
        Chain {
            inner: Arc::new(core::SharedChain::new(chain)),
        }
    }

    /// The hash of the last record chained (the genesis hash before any).
    #[napi]
    pub fn head(&self) -> String {
        self.inner.head()
    }

    /// HMAC-SHA256 of the head under `key`, hex-encoded.
    #[napi]
    pub fn sign_head(&self, key: Buffer) -> String {
        core::sign_head(&self.inner.head(), &key)
    }

    /// Chains `record` (JSON text) now and returns it with `meta.prev_hash` and `meta.hash` set.
    #[napi]
    pub fn stamp(&self, record: String) -> Result<String> {
        let value = parse(&record, "the record")?;
        Ok(self.inner.write(value, Value::clone).to_string())
    }
}

/// HMAC-SHA256 of `head_hash` under `key`, hex-encoded.
#[napi]
pub fn sign_head(head_hash: String, key: Buffer) -> String {
    core::sign_head(&head_hash, &key)
}

/// Whether `signature` is what `signHead(headHash, key)` gives (compared in constant time).
#[napi]
pub fn verify_head_signature(head_hash: String, key: Buffer, signature: String) -> bool {
    core::verify_head_signature(&head_hash, &key, &signature)
}

/// Verifies a chain (JSON text of an array) and that its head matches `signature`.
#[napi]
pub fn verify_signed_chain(
    records: String,
    key: Buffer,
    signature: String,
    genesis: Option<String>,
) -> Result<Verification> {
    let Value::Array(items) = parse(&records, "the records")? else {
        return Err(reason("the records must be a JSON array of record objects"));
    };
    Ok(verification(core::verify_signed_chain(
        items.iter(),
        &key,
        &signature,
        genesis.as_deref().unwrap_or(core::GENESIS_HASH),
    )))
}
