//! The Rust core for the browser, Deno, Bun and Workers: `wasm32-unknown-unknown`, loaded with
//! wasm-bindgen. Same boundary as the Node addon (`bindings/node`): pure helpers take and return JSON
//! **text**, because a JavaScript number cannot tell `1` from `1.0` and the canonical text and the tamper
//! chain depend on it (`schema/corpus/README.md`). Structured results (a verification, an `llm` block) are
//! returned as JSON text too and turned into plain objects by `js/index.mjs`, so the exported names and
//! shapes match the Node addon and one corpus harness runs against both (`docs/stages/stage-7.md`).

mod derive;
mod plugins;
mod state;

use logquill_core as core;
use serde_json::{Map, Value};
use wasm_bindgen::prelude::*;

/// Rewrites the bare words `NaN`, `Infinity` and `-Infinity` (which Python's `json` writes and
/// `serde_json` refuses) to the overflowing literals `1e999` / `-1e999`, which read as the same
/// non-finite value. Words inside string literals are left alone. `JSON.stringify` never writes
/// these words; this is for text produced by Python or by the shared corpus.
///
/// A copy of the function in `bindings/node/src/lib.rs`; the shared corpus keeps the two honest.
fn lenient(text: &str) -> std::borrow::Cow<'_, str> {
    if !text.contains("NaN") && !text.contains("Infinity") {
        return std::borrow::Cow::Borrowed(text);
    }
    let mut out = String::with_capacity(text.len());
    let (mut in_string, mut escaped) = (false, false);
    let mut rest = text;
    while let Some(c) = rest.chars().next() {
        if in_string {
            out.push(c);
            (in_string, escaped) = match (escaped, c) {
                (true, _) => (true, false),
                (false, '\\') => (true, true),
                (false, '"') => (false, false),
                _ => (true, false),
            };
            rest = &rest[c.len_utf8()..];
        } else if c == '"' {
            in_string = true;
            out.push(c);
            rest = &rest[1..];
        } else if let Some(tail) = rest.strip_prefix("-Infinity") {
            out.push_str("-1e999");
            rest = tail;
        } else if let Some(tail) = rest.strip_prefix("Infinity").or(rest.strip_prefix("NaN")) {
            out.push_str("1e999");
            rest = tail;
        } else {
            out.push(c);
            rest = &rest[c.len_utf8()..];
        }
    }
    std::borrow::Cow::Owned(out)
}

pub(crate) fn locked<T>(mutex: &std::sync::Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|p| p.into_inner())
}

pub(crate) fn fail(message: impl AsRef<str>) -> JsError {
    JsError::new(message.as_ref())
}

/// Parses `text` as JSON, keeping every number's own spelling.
pub(crate) fn parse(text: &str, what: &str) -> Result<Value, JsError> {
    serde_json::from_str(&lenient(text)).map_err(|e| {
        fail(format!(
            "{what} is not valid JSON ({e}) — pass the output of JSON.stringify"
        ))
    })
}

/// `text` as a JSON object (a record's `meta`, an options bag, a set of fields).
pub(crate) fn object_of(text: &str, what: &str) -> Result<Map<String, Value>, JsError> {
    match parse(text, what)? {
        Value::Object(map) => Ok(map),
        _ => Err(fail(format!("{what} must be a JSON object"))),
    }
}

/// An options bag handed over as JSON text; every field is optional. The JavaScript side
/// (`js/index.mjs`) stringifies the caller's options object, so the classes keep the Node addon's
/// option-object constructors without wasm-bindgen needing a typed struct for each.
pub(crate) struct Options(Map<String, Value>);

impl Options {
    pub(crate) fn from_json(text: Option<String>) -> Result<Self, JsError> {
        match text.as_deref() {
            None | Some("") => Ok(Self(Map::new())),
            Some(text) => object_of(text, "the options").map(Self),
        }
    }
    pub(crate) fn string(&self, key: &str) -> Option<String> {
        self.0.get(key)?.as_str().map(str::to_string)
    }
    pub(crate) fn f64(&self, key: &str) -> Option<f64> {
        self.0.get(key)?.as_f64()
    }
    pub(crate) fn usize(&self, key: &str) -> Option<usize> {
        self.f64(key).map(|n| n.max(0.0) as usize)
    }
    pub(crate) fn bool(&self, key: &str) -> Option<bool> {
        self.0.get(key)?.as_bool()
    }
    pub(crate) fn strings(&self, key: &str) -> Option<Vec<String>> {
        let list = self.0.get(key)?.as_array()?;
        Some(
            list.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect(),
        )
    }
}

/// The hash a chain starts from, before its first record.
#[wasm_bindgen(js_name = genesisHash)]
pub fn genesis_hash() -> String {
    core::GENESIS_HASH.to_string()
}

/// The current time as a record timestamp: UTC, `YYYY-MM-DDTHH:MM:SS.mmmZ`. The clock is `Date.now()`.
#[wasm_bindgen(js_name = utcTimestamp)]
pub fn utc_timestamp() -> String {
    core::format_timestamp((js_sys::Date::now() * 1000.0) as i64)
}

/// A record timestamp for `unixMicros` microseconds since the epoch: `YYYY-MM-DDTHH:MM:SS.mmmZ`.
#[wasm_bindgen(js_name = formatTimestamp)]
pub fn format_timestamp(unix_micros: f64) -> String {
    core::format_timestamp(unix_micros as i64)
}

/// The canonical text of a JSON document: what the tamper chain hashes.
#[wasm_bindgen(js_name = canonicalJson)]
pub fn canonical_json(json: &str) -> Result<String, JsError> {
    // the core's own reader: it keeps `1.0` a float and reads NaN / Infinity as Python's does (D19)
    core::Canon::parse(json)
        .map(|value| value.to_text())
        .map_err(|e| fail(format!("the value is not valid JSON ({e})")))
}

/// One record (JSON text) as a compact JSON line.
#[wasm_bindgen(js_name = formatJson)]
pub fn format_json(record: &str) -> Result<String, JsError> {
    Ok(core::format_json(&parse(record, "the record")?))
}

/// One record (JSON text) as a human-readable line, with `meta.stack` on the lines after.
#[wasm_bindgen(js_name = formatText)]
pub fn format_text(record: &str) -> Result<String, JsError> {
    Ok(core::format_text(&parse(record, "the record")?))
}

/// One record (JSON text) as `key=value` pairs.
#[wasm_bindgen(js_name = formatLogfmt)]
pub fn format_logfmt(record: &str) -> Result<String, JsError> {
    Ok(core::format_logfmt(&parse(record, "the record")?))
}

/// The fields of a logfmt line, as JSON text of an object.
#[wasm_bindgen(js_name = parseLogfmt)]
pub fn parse_logfmt(line: &str) -> String {
    Value::Object(core::parse_logfmt(line)).to_string()
}

/// Checks a record (JSON text) against the contract and returns it, in current form, as JSON text;
/// a 1.x record is read as version 1.0. Throws an `Error` saying what is wrong.
#[wasm_bindgen(js_name = parseRecord)]
pub fn parse_record(record: &str) -> Result<String, JsError> {
    let parsed =
        core::parse_record(&parse(record, "the record")?).map_err(|e| fail(e.to_string()))?;
    Ok(parsed.to_value().to_string())
}

/// Verifies a hash-chained sequence of records, given as JSON text of an array. The result is JSON text:
/// `{ok, recordsChecked, brokenAt?, reason?, headHash?}` (`js/index.mjs` turns it into an object).
#[wasm_bindgen(js_name = verifyChainJson)]
pub fn verify_chain_json(records: &str, genesis: Option<String>) -> Result<String, JsError> {
    let Value::Array(items) = parse(records, "the records")? else {
        return Err(fail("the records must be a JSON array of record objects"));
    };
    let result = core::verify_chain(
        items.iter(),
        genesis.as_deref().unwrap_or(core::GENESIS_HASH),
    );
    Ok(verification_json(result))
}

pub(crate) fn verification_json(result: core::Verification) -> String {
    let mut out = serde_json::Map::new();
    out.insert("ok".into(), Value::Bool(result.ok));
    out.insert("recordsChecked".into(), result.records_checked.into());
    if let Some(n) = result.broken_at {
        out.insert("brokenAt".into(), n.into());
    }
    if let Some(reason) = result.reason {
        out.insert("reason".into(), reason.into());
    }
    if let Some(head) = result.head_hash {
        out.insert("headHash".into(), head.into());
    }
    Value::Object(out).to_string()
}
