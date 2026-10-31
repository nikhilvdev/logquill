//! The three line formats and the logfmt reader (`docs/spec/04-formatters-config-parsing.md`;
//! decisions D4, D31, D32, D42).

use std::sync::OnceLock;

use regex::{Captures, Regex};
use serde_json::{Map, Value};

use crate::jsonx::json_line;

fn meta_of(record: &Value) -> Option<&Map<String, Value>> {
    record.get("meta").and_then(Value::as_object)
}

/// A field as it prints: its text, or `default` when missing or `null`; other JSON as JSON.
pub(crate) fn shown(value: Option<&Value>, default: &str) -> String {
    match value {
        None | Some(Value::Null) => default.to_string(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => json_line(other),
    }
}

/// One JSON line: compact, UTF-8, keys in record order.
pub fn format_json(record: &Value) -> String {
    json_line(record)
}

/// One human-readable entry: `<timestamp> <LEVEL:5> <logger>: <message>[ llm=…][ {meta}]`, with
/// `meta.stack` on the lines after. Missing fields print as `?` (the message as empty).
pub fn format_text(record: &Value) -> String {
    let mut meta = meta_of(record).cloned().unwrap_or_default();
    let stack = match meta.get("stack") {
        Some(Value::String(s)) if !s.is_empty() => {
            let s = s.clone();
            meta.shift_remove("stack");
            Some(s)
        }
        _ => None,
    };
    let level = shown(record.get("level"), "?");
    let mut line = format!(
        "{} {:<5} {}: {}",
        shown(record.get("timestamp"), "?"),
        level,
        shown(record.get("logger"), "?"),
        shown(record.get("message"), "")
    );
    if let Some(llm) = record
        .get("llm")
        .filter(|v| v.as_object().is_some_and(|o| !o.is_empty()))
    {
        line.push_str(&format!(" llm={}", json_line(llm)));
    }
    if !meta.is_empty() {
        line.push_str(&format!(" {}", json_line(&Value::Object(meta))));
    }
    if let Some(stack) = stack {
        line.push('\n');
        line.push_str(stack.trim_end_matches('\n'));
    }
    line
}

fn needs_escape(c: char) -> bool {
    (c as u32) < 0x20 || matches!(c, '\u{7f}' | '\u{85}' | '\u{2028}' | '\u{2029}')
}

fn triggers(c: char) -> bool {
    needs_escape(c) || matches!(c, ' ' | '"' | '=' | '\\')
}

fn quote(text: &str) -> String {
    if !text.is_empty() && !text.chars().any(triggers) {
        return text.to_string();
    }
    let mut out = String::from("\"");
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if needs_escape(c) => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

fn key(raw: &str) -> String {
    let cleaned: String = raw
        .chars()
        .map(|c| if triggers(c) { '_' } else { c })
        .collect();
    if cleaned.is_empty() {
        "_".to_string()
    } else {
        cleaned
    }
}

fn scalar(value: &Value) -> String {
    match value {
        Value::Null => "null".to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Number(_) => json_line(value),
        Value::String(s) => quote(s),
        other => quote(&json_line(other)),
    }
}

fn flatten(prefix: &str, value: &Value, depth: usize, out: &mut Vec<String>) {
    match value {
        Value::Object(map) if !map.is_empty() && depth < 5 => {
            for (child_key, child) in map {
                flatten(
                    &format!("{prefix}.{}", key(child_key)),
                    child,
                    depth + 1,
                    out,
                );
            }
        }
        _ => out.push(format!("{prefix}={}", scalar(value))),
    }
}

/// One `key=value` line, always on a single line: the four record fields, then `llm.*`, then
/// each `meta` key (nested objects flattened to dotted keys up to depth 5).
pub fn format_logfmt(record: &Value) -> String {
    let mut pairs = vec![
        format!("timestamp={}", quote(&shown(record.get("timestamp"), "?"))),
        format!("level={}", quote(&shown(record.get("level"), "?"))),
        format!("logger={}", quote(&shown(record.get("logger"), "?"))),
        format!("message={}", quote(&shown(record.get("message"), ""))),
    ];
    if let Some(llm) = record
        .get("llm")
        .filter(|v| v.as_object().is_some_and(|o| !o.is_empty()))
    {
        flatten("llm", llm, 1, &mut pairs);
    }
    if let Some(meta) = meta_of(record) {
        for (raw, value) in meta {
            let mut name = key(raw);
            if matches!(name.as_str(), "timestamp" | "level" | "logger" | "message") {
                name = format!("meta.{name}");
            }
            flatten(&name, value, 1, &mut pairs);
        }
    }
    pairs.join(" ")
}

fn token_pattern() -> &'static Regex {
    static TOKEN: OnceLock<Regex> = OnceLock::new();
    TOKEN.get_or_init(|| {
        Regex::new(r#"([^ \t\n\r\x0b\x0c="]+)(?:=("(?:[^"\\]|\\.)*"|[^ \t\n\r\x0b\x0c"]*))?"#)
            .expect("a built-in pattern is valid")
    })
}

fn escape_pattern() -> &'static Regex {
    static ESCAPE: OnceLock<Regex> = OnceLock::new();
    ESCAPE.get_or_init(|| {
        Regex::new(
            r"(?s)\\(u[dD][89abAB][0-9a-fA-F]{2}\\u[dD][c-fC-F][0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)",
        )
        .expect("a built-in pattern is valid")
    })
}

fn unescape(found: &Captures) -> String {
    let whole = found[0].to_string();
    let code = &found[1];
    if code.len() == 11 {
        let high = u32::from_str_radix(&code[1..5], 16).unwrap_or(0);
        let low = u32::from_str_radix(&code[7..11], 16).unwrap_or(0);
        return char::from_u32(0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00))
            .map_or(whole, String::from);
    }
    if code.len() == 5 && code.starts_with('u') {
        let point = u32::from_str_radix(&code[1..], 16).unwrap_or(0);
        return if (0xD800..=0xDFFF).contains(&point) {
            whole
        } else {
            char::from_u32(point).map_or(whole, String::from)
        };
    }
    match code {
        "n" => "\n".into(),
        "r" => "\r".into(),
        "t" => "\t".into(),
        "\"" => "\"".into(),
        "\\" => "\\".into(),
        _ => whole,
    }
}

/// Reads a logfmt line back into text values. A bare key maps to `""`, a repeated key keeps its
/// last value, a quoted value is unescaped.
pub fn parse_logfmt(line: &str) -> Map<String, Value> {
    let mut fields = Map::new();
    for found in token_pattern().captures_iter(line) {
        let name = found[1].to_string();
        let value = match found.get(2).map(|m| m.as_str()) {
            None => String::new(),
            Some(raw) if raw.len() >= 2 && raw.starts_with('"') && raw.ends_with('"') => {
                escape_pattern()
                    .replace_all(&raw[1..raw.len() - 1], |c: &Captures| unescape(c))
                    .into_owned()
            }
            Some(raw) => raw.to_string(),
        };
        fields.insert(name, Value::String(value));
    }
    fields
}
