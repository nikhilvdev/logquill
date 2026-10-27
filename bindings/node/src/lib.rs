//! The native addon behind the `logquill` npm package: the Rust core, called from Node.
//!
//! This first slice exposes the pure helpers (formatting, parsing, canonical JSON, chain
//! verification). They take and return JSON **text**, not JavaScript values: a JavaScript number
//! cannot tell `1` from `1.0`, and the canonical text and the tamper chain depend on that
//! difference (`schema/corpus/README.md`). The `Logger` and the runtime come in later slices
//! (`docs/stages/stage-6.md`).

#[cfg(feature = "runtime")]
mod alerts;
mod derive;
#[cfg(feature = "runtime")]
mod dispatch;
#[cfg(feature = "runtime")]
mod metrics;
mod plugins;
#[cfg(feature = "runtime")]
mod sinks;
mod state;
#[cfg(feature = "runtime")]
mod watchers;

use logquill_core as core;
use napi::{Error, Result};
use napi_derive::napi;
use serde_json::Value;

/// Rewrites the bare words `NaN`, `Infinity` and `-Infinity` (which Python's `json` writes and
/// `serde_json` refuses) to the overflowing literals `1e999` / `-1e999`, which read as the same
/// non-finite value. Words inside string literals are left alone. `JSON.stringify` never writes
/// these words; this is for text produced by Python or by the shared corpus.
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

/// Parses `text` as JSON, keeping every number's own spelling.
pub(crate) fn parse(text: &str, what: &str) -> Result<Value> {
    serde_json::from_str(&lenient(text)).map_err(|e| {
        Error::from_reason(format!(
            "{what} is not valid JSON ({e}) — pass the output of JSON.stringify"
        ))
    })
}

/// The hash a chain starts from, before its first record.
#[napi(js_name = "GENESIS_HASH")]
pub const GENESIS_HASH: &str = core::GENESIS_HASH;

/// The current time as a record timestamp: UTC, `YYYY-MM-DDTHH:MM:SS.mmmZ`.
#[napi]
pub fn utc_timestamp() -> String {
    use core::Clock;
    core::format_timestamp(core::SystemClock.now_unix_micros())
}

/// The canonical text of a JSON document: what the tamper chain hashes.
#[napi]
pub fn canonical_json(json: String) -> Result<String> {
    // the core's own reader: it keeps `1.0` a float and reads NaN / Infinity as Python's does (D19)
    core::Canon::parse(&json)
        .map(|value| value.to_text())
        .map_err(|e| Error::from_reason(format!("the value is not valid JSON ({e})")))
}

/// One record (JSON text) as a compact JSON line.
#[napi]
pub fn format_json(record: String) -> Result<String> {
    Ok(core::format_json(&parse(&record, "the record")?))
}

/// One record (JSON text) as a human-readable line, with `meta.stack` on the lines after.
#[napi]
pub fn format_text(record: String) -> Result<String> {
    Ok(core::format_text(&parse(&record, "the record")?))
}

/// One record (JSON text) as `key=value` pairs.
#[napi]
pub fn format_logfmt(record: String) -> Result<String> {
    Ok(core::format_logfmt(&parse(&record, "the record")?))
}

/// The fields of a logfmt line, as JSON text of an object.
#[napi]
pub fn parse_logfmt(line: String) -> String {
    Value::Object(core::parse_logfmt(&line)).to_string()
}

/// Checks a record (JSON text) against the contract and returns it, in current form, as JSON text;
/// a 1.x record is read as version 1.0. Throws an `Error` saying what is wrong.
#[napi]
pub fn parse_record(record: String) -> Result<String> {
    let parsed = core::parse_record(&parse(&record, "the record")?)
        .map_err(|e| Error::from_reason(e.to_string()))?;
    Ok(parsed.to_value().to_string())
}

/// What verifying a hash chain found.
#[napi(object)]
pub struct Verification {
    pub ok: bool,
    pub records_checked: u32,
    /// 1-based position of the first record that failed, if any.
    pub broken_at: Option<u32>,
    pub reason: Option<String>,
    pub head_hash: Option<String>,
}

/// Verifies a hash-chained sequence of records, given as JSON text of an array.
#[napi]
pub fn verify_chain(records: String, genesis: Option<String>) -> Result<Verification> {
    let Value::Array(items) = parse(&records, "the records")? else {
        return Err(Error::from_reason(
            "the records must be a JSON array of record objects",
        ));
    };
    let result = core::verify_chain(
        items.iter(),
        genesis.as_deref().unwrap_or(core::GENESIS_HASH),
    );
    Ok(verification(result))
}

pub(crate) fn verification(result: core::Verification) -> Verification {
    Verification {
        ok: result.ok,
        records_checked: u32::try_from(result.records_checked).unwrap_or(u32::MAX),
        broken_at: result
            .broken_at
            .map(|n| u32::try_from(n).unwrap_or(u32::MAX)),
        reason: result.reason,
        head_hash: result.head_hash,
    }
}
