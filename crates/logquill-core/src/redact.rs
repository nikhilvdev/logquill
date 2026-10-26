//! Key redaction and pattern (PII) redaction over `meta` (`docs/spec/02-plugins.md`).
//!
//! Both walk nested objects and lists to a depth of 50. Anything deeper that could hold data is
//! replaced rather than passed through (decision D41), and pattern classes are ASCII-only.

use std::collections::HashSet;

use regex::Regex;
use serde_json::{Map, Value};

use crate::policy::{FieldClass, FIELD_CLASSES};

/// How deep redaction looks before it replaces what it cannot inspect.
pub const MAX_DEPTH: usize = 50;

/// The `secret`-classed keys, the default for [`KeyRedactor`].
pub const DEFAULT_KEYS: [&str; 5] = ["password", "token", "secret", "api_key", "authorization"];

/// Replaces the value of any `meta` key (case-insensitively, at any depth) that is listed.
#[derive(Clone, Debug)]
pub struct KeyRedactor {
    keys: HashSet<String>,
    replacement: String,
}

impl KeyRedactor {
    /// Redacts `keys` plus every well-known key of the given `classes`.
    pub fn new<'a>(
        keys: impl IntoIterator<Item = &'a str>,
        classes: &[FieldClass],
        replacement: &str,
    ) -> KeyRedactor {
        let mut set: HashSet<String> = keys.into_iter().map(str::to_lowercase).collect();
        for (key, class) in FIELD_CLASSES {
            if classes.contains(&class) {
                set.insert(key.to_string());
            }
        }
        KeyRedactor {
            keys: set,
            replacement: replacement.to_string(),
        }
    }

    /// The default: the `secret` keys, replaced by `***`.
    pub fn secrets() -> KeyRedactor {
        KeyRedactor::new(DEFAULT_KEYS, &[], "***")
    }

    /// Redacts `meta` in place.
    pub fn redact_meta(&self, meta: &mut Map<String, Value>) {
        self.walk_object(meta, 0);
    }

    /// For a captured local variable: the replacement if its name is listed, else the text.
    pub fn redact_local(&self, name: &str, text: &str) -> String {
        if self.keys.contains(&name.to_lowercase()) {
            self.replacement.clone()
        } else {
            text.to_string()
        }
    }

    fn replace(&self) -> Value {
        Value::String(self.replacement.clone())
    }

    fn walk_object(&self, object: &mut Map<String, Value>, depth: usize) {
        for (key, value) in object.iter_mut() {
            if self.keys.contains(&key.to_lowercase()) {
                *value = self.replace();
            } else {
                self.walk(value, depth + 1);
            }
        }
    }

    fn walk(&self, value: &mut Value, depth: usize) {
        match value {
            Value::String(_) | Value::Array(_) | Value::Object(_) if depth > MAX_DEPTH => {
                *value = self.replace();
            }
            Value::Object(object) => self.walk_object(object, depth),
            Value::Array(items) => items.iter_mut().for_each(|item| self.walk(item, depth + 1)),
            _ => {}
        }
    }
}

const SPACE: &str = r"[ \t\n\r\x0b\x0c]";

fn patterns() -> Vec<Regex> {
    let b = r"(?-u:\b)";
    let sources = [
        format!(r"{b}[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{{2,}}{b}"),
        format!(r"{b}[0-9]{{3}}-[0-9]{{2}}-[0-9]{{4}}{b}"),
        format!(r"{b}(?:[0-9][ -]?){{13,16}}{b}"),
        format!(r"{b}(?:\+?1[-. \t\n\r\x0b\x0c]?)?\(?[0-9]{{3}}\)?[-. \t\n\r\x0b\x0c]?[0-9]{{3}}[-. \t\n\r\x0b\x0c]?[0-9]{{4}}{b}"),
        format!(r"{b}sk-(?:proj-)?[A-Za-z0-9_-]{{20,}}{b}"),
        format!(r"{b}sk-ant-[A-Za-z0-9_-]{{20,}}{b}"),
        format!(r"{b}(?:AKIA|ASIA)[0-9A-Z]{{16}}{b}"),
        format!(r"{b}gh[pousr]_[A-Za-z0-9]{{36,}}{b}"),
        format!(r"{b}xox[baprs]-[A-Za-z0-9-]{{10,}}{b}"),
        format!(r"{b}eyJ[A-Za-z0-9_-]{{10,}}\.[A-Za-z0-9_-]{{10,}}\.[A-Za-z0-9_-]{{10,}}{b}"),
        r"(?s)-----BEGIN (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----".to_string(),
        format!(r"{b}Bearer{SPACE}+[A-Za-z0-9\-_.]{{20,}}{b}"),
    ];
    sources
        .iter()
        .map(|s| Regex::new(s).expect("a built-in pattern is valid"))
        .collect()
}

/// Replaces personal-data and credential shapes inside string values, whatever key holds them.
#[derive(Clone, Debug)]
pub struct PiiRedactor {
    patterns: Vec<Regex>,
    replacement: String,
}

impl PiiRedactor {
    /// The twelve built-in patterns (email, SSN, card number, phone, OpenAI / Anthropic / AWS /
    /// GitHub / Slack keys, JWT, private-key block, bearer token).
    pub fn new(replacement: &str) -> PiiRedactor {
        PiiRedactor {
            patterns: patterns(),
            replacement: replacement.to_string(),
        }
    }

    /// Replaces every match. Matches are found independently over the original text; the
    /// leftmost wins (the longer one on a tie) and overlapping later ones are skipped.
    pub fn redact_text(&self, text: &str) -> String {
        let mut spans: Vec<(usize, usize)> = self
            .patterns
            .iter()
            .flat_map(|pattern| pattern.find_iter(text).map(|m| (m.start(), m.end())))
            .collect();
        if spans.is_empty() {
            return text.to_string();
        }
        spans.sort_by_key(|&(start, end)| (start, std::cmp::Reverse(end)));
        let mut out = String::with_capacity(text.len());
        let (mut cursor, mut last_end) = (0, 0);
        let mut any = false;
        for (start, end) in spans {
            if !any || start >= last_end {
                out.push_str(&text[cursor..start]);
                out.push_str(&self.replacement);
                cursor = end;
                last_end = end;
                any = true;
            }
        }
        out.push_str(&text[cursor..]);
        out
    }

    /// Redacts every string in `meta`, to a depth of 50.
    pub fn redact_meta(&self, meta: &mut Map<String, Value>) {
        for value in meta.values_mut() {
            self.walk(value, 1);
        }
    }

    fn walk(&self, value: &mut Value, depth: usize) {
        match value {
            Value::String(_) | Value::Array(_) | Value::Object(_) if depth > MAX_DEPTH => {
                *value = Value::String(self.replacement.clone());
            }
            Value::String(text) => *text = self.redact_text(text),
            Value::Object(object) => object.values_mut().for_each(|v| self.walk(v, depth + 1)),
            Value::Array(items) => items.iter_mut().for_each(|v| self.walk(v, depth + 1)),
            _ => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn nested_keys_are_redacted_in_any_case() {
        let mut meta = json!({"a": {"Password": "p", "ok": 1}, "list": [{"TOKEN": "t"}]})
            .as_object()
            .unwrap()
            .clone();
        KeyRedactor::secrets().redact_meta(&mut meta);
        assert_eq!(
            Value::Object(meta),
            json!({"a": {"Password": "***", "ok": 1}, "list": [{"TOKEN": "***"}]})
        );
    }

    #[test]
    fn pii_in_a_value_is_replaced_whatever_the_key() {
        let redactor = PiiRedactor::new("***");
        assert_eq!(
            redactor.redact_text("mail jane@example.com now"),
            "mail *** now"
        );
        assert_eq!(redactor.redact_text("\u{e9}user@example.com"), "\u{e9}***");
        assert_eq!(redactor.redact_text("no secrets"), "no secrets");
    }
}
