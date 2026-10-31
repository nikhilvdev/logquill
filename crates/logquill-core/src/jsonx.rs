//! Small helpers over `serde_json::Value` shared by the formatters, mappers and plugins.

use serde_json::{Number, Value};

use crate::canonical::python_float_repr;

/// Whether the number was written as an integer (no fraction or exponent).
pub(crate) fn is_integer(value: &Value) -> bool {
    matches!(value, Value::Number(n) if !n.to_string().bytes().any(|b| matches!(b, b'.' | b'e' | b'E')))
}

/// A float as a JSON number spelled the way Python prints it (`1.0`, `1e-05`).
pub(crate) fn float_value(x: f64) -> Value {
    if !x.is_finite() {
        return Value::Null;
    }
    serde_json::from_str::<Number>(&python_float_repr(x)).map_or(Value::Null, Value::Number)
}

/// The JSON type name used in messages: null, boolean, number, string, array, object.
pub(crate) fn json_type(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

/// A number's text in a log line: integers verbatim, floats as Python spells them, and
/// non-finite values as `null`.
fn number_text(lexeme: &str) -> String {
    if lexeme.bytes().any(|b| matches!(b, b'.' | b'e' | b'E')) {
        match lexeme.parse::<f64>() {
            Ok(x) if x.is_finite() => python_float_repr(x),
            _ => "null".to_string(),
        }
    } else if lexeme == "-0" {
        "0".to_string()
    } else {
        lexeme.to_string()
    }
}

fn push_string(text: &str, out: &mut String) {
    out.push('"');
    for c in text.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

fn push_value(value: &Value, out: &mut String) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => out.push_str(&number_text(&n.to_string())),
        Value::String(s) => push_string(s, out),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                push_value(item, out);
            }
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');
            for (i, (key, item)) in map.iter().enumerate() {
                if i > 0 {
                    out.push(',');
                }
                push_string(key, out);
                out.push(':');
                push_value(item, out);
            }
            out.push('}');
        }
    }
}

/// The compact JSON line for a value: UTF-8, keys in the order given, floats as Python prints
/// them, non-finite numbers as `null`.
pub fn json_line(value: &Value) -> String {
    let mut out = String::new();
    push_value(value, &mut out);
    out
}

/// Rewrites the bare words `NaN`, `Infinity` and `-Infinity` (which Python's `json` writes and `serde_json` refuses) to
/// the overflowing literals `1e999` / `-1e999`, which read as the same non-finite value. Words inside string literals are
/// left alone. `JSON.stringify` never writes these words; this is for text produced by Python or by the shared corpus.
pub fn lenient(text: &str) -> std::borrow::Cow<'_, str> {
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

/// Parses `text` as JSON, keeping every number's own spelling and accepting `NaN` / `Infinity` (see [`lenient`]).
pub fn parse_lenient(text: &str) -> Result<Value, serde_json::Error> {
    serde_json::from_str(&lenient(text))
}

#[cfg(test)]
mod lenient_tests {
    use super::*;

    #[test]
    fn reads_the_words_python_writes() {
        let v = parse_lenient(r#"{"a": NaN, "b": -Infinity, "c": [Infinity]}"#).unwrap();
        assert!(v["a"].is_number() && v["b"].is_number() && v["c"][0].is_number());
    }

    #[test]
    fn leaves_the_words_inside_strings_alone() {
        let v = parse_lenient(r#"{"m": "NaN and \"Infinity\" are words"}"#).unwrap();
        assert_eq!(v["m"], "NaN and \"Infinity\" are words");
    }

    #[test]
    fn keeps_a_numbers_own_spelling() {
        let v = parse_lenient(r#"{"x": 1.0, "y": 1}"#).unwrap();
        assert_eq!(v["x"].to_string(), "1.0");
        assert_eq!(v["y"].to_string(), "1");
    }

    #[test]
    fn plain_text_is_not_copied() {
        assert!(matches!(
            lenient(r#"{"a": 1}"#),
            std::borrow::Cow::Borrowed(_)
        ));
    }

    #[test]
    fn rejects_text_that_is_not_json() {
        assert!(parse_lenient("not json").is_err());
    }
}
