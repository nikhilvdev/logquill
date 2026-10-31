//! Reading trace ids from request headers, and deriving OpenTelemetry ids from LogQuill ids
//! (`docs/spec/02-plugins.md`, `docs/spec/03-privacy-tracing-otel.md`; decision D40).

use std::sync::OnceLock;

use regex::Regex;
use serde_json::{Map, Value};
use sha2::{Digest, Sha256};

/// ASCII whitespace, the only thing trimmed from a header.
const ASCII_SPACE: &[char] = &[' ', '\t', '\n', '\r', '\x0b', '\x0c'];

fn lazy(cell: &'static OnceLock<Regex>, source: &str) -> &'static Regex {
    cell.get_or_init(|| Regex::new(source).expect("a built-in pattern is valid"))
}

fn is_zero(hex: &str) -> bool {
    hex.bytes().all(|b| b == b'0')
}

/// The 32-hex trace id in a W3C `traceparent`, AWS `X-Amzn-Trace-Id` or GCP
/// `X-Cloud-Trace-Context` value; `None` for anything else, including all-zero ids and
/// `traceparent` version `ff`.
pub fn parse_trace_header(header: &str) -> Option<String> {
    static W3C: OnceLock<Regex> = OnceLock::new();
    static XRAY: OnceLock<Regex> = OnceLock::new();
    static GCP: OnceLock<Regex> = OnceLock::new();
    let text = header.trim_matches(ASCII_SPACE);
    if let Some(found) = lazy(
        &W3C,
        r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-[0-9a-f]{2}$",
    )
    .captures(text)
    {
        let (version, trace, parent) = (&found[1], &found[2], &found[3]);
        let valid = version != "ff" && !is_zero(trace) && !is_zero(parent);
        return valid.then(|| trace.to_string());
    }
    if let Some(found) = lazy(&XRAY, r"Root=1-([0-9a-f]{8})-([0-9a-f]{24})").captures(text) {
        let trace = format!("{}{}", &found[1], &found[2]);
        return (!is_zero(&trace)).then_some(trace);
    }
    if let Some(found) = lazy(&GCP, r"^([0-9a-f]{32})/[0-9]+(;o=[0-9])?$").captures(text) {
        let trace = found[1].to_string();
        return (!is_zero(&trace)).then_some(trace);
    }
    None
}

fn digest_hex(text: &str, bytes: usize) -> String {
    let digest = Sha256::digest(text.as_bytes());
    let raw = &digest[..bytes];
    if raw.iter().all(|b| *b == 0) {
        return format!("{}1", "0".repeat(bytes * 2 - 1));
    }
    raw.iter().map(|b| format!("{b:02x}")).collect()
}

fn is_hex_id(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        && !is_zero(value)
}

/// A 16-hex OpenTelemetry span id for a LogQuill span id: the id itself if it already is one,
/// otherwise the first 8 bytes of its SHA-256.
pub fn span_id_for(value: &str) -> String {
    if is_hex_id(value, 16) {
        value.to_string()
    } else {
        digest_hex(value, 8)
    }
}

/// The 32-hex trace id a record belongs to: `meta.trace_id` if valid, else derived from
/// `meta.run_id`, else none.
pub fn trace_id_for(meta: &Map<String, Value>) -> Option<String> {
    if let Some(Value::String(trace)) = meta.get("trace_id") {
        if is_hex_id(trace, 32) {
            return Some(trace.clone());
        }
    }
    match meta.get("run_id") {
        Some(Value::String(run)) if !run.is_empty() => Some(digest_hex(&format!("run:{run}"), 16)),
        _ => None,
    }
}

/// A trace id for spans that name a parent but no run or trace.
pub fn trace_id_from_parent(parent_span_id: &str) -> String {
    digest_hex(&format!("parent:{parent_span_id}"), 16)
}
