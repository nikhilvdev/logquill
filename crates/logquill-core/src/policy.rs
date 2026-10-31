//! Field classes and the content-capture policy (`docs/spec/03-privacy-tracing-otel.md`).

use std::fmt;

use serde_json::{Map, Value};

use crate::canonical::canonical_json;
use crate::chain::sha256_hex;

/// How a well-known `meta` key is treated.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FieldClass {
    /// Always redacted.
    Secret,
    /// Scanned for personal-data patterns.
    Pii,
    /// Prompt, completion and tool data, governed by the content policy.
    Content,
}

impl FieldClass {
    /// The class name used in configuration and fixtures.
    pub const fn name(self) -> &'static str {
        match self {
            FieldClass::Secret => "secret",
            FieldClass::Pii => "pii",
            FieldClass::Content => "content",
        }
    }
}

/// Well-known keys and their class, in registry order.
pub const FIELD_CLASSES: [(&str, FieldClass); 14] = [
    ("password", FieldClass::Secret),
    ("token", FieldClass::Secret),
    ("secret", FieldClass::Secret),
    ("api_key", FieldClass::Secret),
    ("authorization", FieldClass::Secret),
    ("email", FieldClass::Pii),
    ("ssn", FieldClass::Pii),
    ("phone", FieldClass::Pii),
    ("input_messages", FieldClass::Content),
    ("output_messages", FieldClass::Content),
    ("system_instructions", FieldClass::Content),
    ("tool_arguments", FieldClass::Content),
    ("tool_result", FieldClass::Content),
    ("state_diff", FieldClass::Content),
];

/// The `content`-classed keys, in registry order.
pub const CONTENT_FIELDS: [&str; 6] = [
    "input_messages",
    "output_messages",
    "system_instructions",
    "tool_arguments",
    "tool_result",
    "state_diff",
];

/// Every key the registry gives `class`, sorted.
pub fn keys_in_class(class: FieldClass) -> Vec<&'static str> {
    let mut keys: Vec<_> = FIELD_CLASSES
        .iter()
        .filter(|(_, c)| *c == class)
        .map(|(k, _)| *k)
        .collect();
    keys.sort_unstable();
    keys
}

/// What to do with content fields before any plugin or transport sees the record.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum ContentPolicy {
    /// Replace with a placeholder (the default).
    #[default]
    Off,
    /// Replace with `sha256:` and the digest of the canonical text.
    Hash,
    /// Keep the canonical text, cut to 200 characters.
    Truncate,
    /// Leave untouched.
    Full,
}

/// An unrecognised policy name.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct UnknownPolicy(pub String);

impl fmt::Display for UnknownPolicy {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "content policy must be one of \"off\", \"hash\", \"truncate\", \"full\", got {}",
            Value::String(self.0.clone())
        )
    }
}

impl std::error::Error for UnknownPolicy {}

/// What replaces a content value under [`ContentPolicy::Off`].
pub const OFF_PLACEHOLDER: &str =
    "<content capture is off — set the logger's content policy to capture it>";

/// How much of a value's text survives [`ContentPolicy::Truncate`].
pub const TRUNCATE_CHARS: usize = 200;

impl ContentPolicy {
    /// The policy named `name` (exact, lower case).
    pub fn parse(name: &str) -> Result<ContentPolicy, UnknownPolicy> {
        match name {
            "off" => Ok(ContentPolicy::Off),
            "hash" => Ok(ContentPolicy::Hash),
            "truncate" => Ok(ContentPolicy::Truncate),
            "full" => Ok(ContentPolicy::Full),
            _ => Err(UnknownPolicy(name.to_string())),
        }
    }

    /// Applies the policy to one value.
    pub fn apply(self, value: &Value) -> Value {
        match self {
            ContentPolicy::Full => value.clone(),
            ContentPolicy::Off => Value::String(OFF_PLACEHOLDER.to_string()),
            ContentPolicy::Hash => {
                Value::String(format!("sha256:{}", sha256_hex(&canonical_json(value))))
            }
            ContentPolicy::Truncate => {
                let text = canonical_json(value);
                let length = text.chars().count();
                if length <= TRUNCATE_CHARS {
                    Value::String(text)
                } else {
                    let head: String = text.chars().take(TRUNCATE_CHARS).collect();
                    Value::String(format!("{head}...(truncated, {length} chars total)"))
                }
            }
        }
    }
}

/// Applies `policy` in place to every content field present in `meta`. `Full` does nothing.
pub fn apply_content_policy(meta: &mut Map<String, Value>, policy: ContentPolicy) {
    if policy == ContentPolicy::Full {
        return;
    }
    for field in CONTENT_FIELDS {
        if let Some(value) = meta.get_mut(field) {
            *value = policy.apply(value);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn off_replaces_and_never_drops() {
        let mut meta = json!({"input_messages": "secret", "keep": 1})
            .as_object()
            .unwrap()
            .clone();
        apply_content_policy(&mut meta, ContentPolicy::Off);
        assert_eq!(meta["input_messages"], OFF_PLACEHOLDER);
        assert_eq!(meta["keep"], 1);
    }

    #[test]
    fn registry_groups_are_consistent() {
        assert_eq!(
            keys_in_class(FieldClass::Secret),
            ["api_key", "authorization", "password", "secret", "token"]
        );
        let content: Vec<_> = FIELD_CLASSES
            .iter()
            .filter(|(_, c)| *c == FieldClass::Content)
            .map(|(k, _)| *k)
            .collect();
        assert_eq!(content, CONTENT_FIELDS);
    }

    #[test]
    fn a_bad_policy_name_says_what_is_valid() {
        let error = ContentPolicy::parse("OFF").unwrap_err();
        assert_eq!(
            error.to_string(),
            "content policy must be one of \"off\", \"hash\", \"truncate\", \"full\", got \"OFF\""
        );
    }
}
