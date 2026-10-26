//! The log record: building one and reading one back (`docs/spec/01-records-and-logger.md`).

use std::fmt;

use serde_json::{Map, Value};

use crate::level::Level;
use crate::time::{format_timestamp, Clock};
use crate::{LEGACY_SCHEMA_VERSION, SCHEMA_VERSION};

/// The free-form `meta` object (keys keep their insertion order).
pub type Meta = Map<String, Value>;

/// Keys a record of this version defines; anything else is carried in [`Record::extra`].
const KNOWN_KEYS: [&str; 7] = [
    "schema_version",
    "timestamp",
    "level",
    "logger",
    "message",
    "meta",
    "llm",
];

/// `meta` keys logquill-js 1.x wrote in camelCase; 2.x writes them in snake_case. Only these
/// are renamed, and only when reading a record that has no `schema_version`.
const LEGACY_META_KEYS: [(&str, &str); 6] = [
    ("runId", "run_id"),
    ("spanId", "span_id"),
    ("parentSpanId", "parent_span_id"),
    ("traceId", "trace_id"),
    ("durationMs", "duration_ms"),
    ("prevHash", "prev_hash"),
];

/// One log record, in the shape every language writes.
#[derive(Clone, Debug, PartialEq)]
pub struct Record {
    pub schema_version: String,
    pub timestamp: String,
    pub level: Level,
    pub logger: String,
    pub message: String,
    pub meta: Meta,
    /// Present only on LLM-call records.
    pub llm: Option<Meta>,
    /// Keys this version does not know, kept so a record from a newer minor version passes
    /// through without losing data.
    pub extra: Meta,
}

impl Record {
    /// Builds a record stamped with the current schema version and the clock's time.
    pub fn new(
        clock: &dyn Clock,
        level: Level,
        logger: impl Into<String>,
        message: impl Into<String>,
        meta: Meta,
        llm: Option<Meta>,
    ) -> Record {
        Record {
            schema_version: SCHEMA_VERSION.to_string(),
            timestamp: format_timestamp(clock.now_unix_micros()),
            level,
            logger: logger.into(),
            message: message.into(),
            meta,
            llm,
            extra: Meta::new(),
        }
    }

    /// The record as a JSON value with its keys in contract order: `schema_version`,
    /// `timestamp`, `level`, `logger`, `message`, `meta`, `llm` (if any), then any extras.
    pub fn to_value(&self) -> Value {
        let mut map = Map::new();
        map.insert(
            "schema_version".into(),
            Value::String(self.schema_version.clone()),
        );
        map.insert("timestamp".into(), Value::String(self.timestamp.clone()));
        map.insert("level".into(), Value::String(self.level.name().to_string()));
        map.insert("logger".into(), Value::String(self.logger.clone()));
        map.insert("message".into(), Value::String(self.message.clone()));
        map.insert("meta".into(), Value::Object(self.meta.clone()));
        if let Some(llm) = &self.llm {
            map.insert("llm".into(), Value::Object(llm.clone()));
        }
        for (key, value) in &self.extra {
            map.insert(key.clone(), value.clone());
        }
        Value::Object(map)
    }
}

/// Why a decoded JSON value is not a readable record. `Display` gives the message every
/// language uses; values are shown as JSON.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum RecordError {
    NotAnObject { got: &'static str },
    BadSchemaVersion { got: String },
    NewerMajorVersion { version: String },
    FieldNotAString { field: &'static str, got: String },
    UnknownLevel { got: String },
    MetaNotAnObject { got: &'static str },
    LlmNotAnObject { got: &'static str },
}

impl fmt::Display for RecordError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            RecordError::NotAnObject { got } => {
                write!(f, "a log record must be a JSON object, got {got}")
            }
            RecordError::BadSchemaVersion { got } => write!(
                f,
                "schema_version must be a string like '{SCHEMA_VERSION}', got {got} — is this a LogQuill record?"
            ),
            RecordError::NewerMajorVersion { version } => write!(
                f,
                "this record has schema_version {}, newer than the '{SCHEMA_VERSION}' this logquill understands — upgrade logquill to read it",
                Value::String(version.clone())
            ),
            RecordError::FieldNotAString { field, got } => {
                write!(f, "record field '{field}' must be a string, got {got}")
            }
            RecordError::UnknownLevel { got } => write!(
                f,
                "record level {} isn't one of TRACE, DEBUG, INFO, WARN, ERROR, FATAL (upper case)",
                Value::String(got.clone())
            ),
            RecordError::MetaNotAnObject { got } => {
                write!(f, "record field 'meta' must be an object, got {got}")
            }
            RecordError::LlmNotAnObject { got } => {
                write!(f, "record field 'llm' must be an object, got {got}")
            }
        }
    }
}

impl std::error::Error for RecordError {}

/// The JSON type name used in error messages.
fn type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

/// `"<major>.<minor>"` with plain decimal digits. A part too long for `u64` reads as the
/// largest value, so such a version is refused as "newer".
fn parse_version(version: &str) -> Option<(u64, u64)> {
    let (major, minor) = version.split_once('.')?;
    let digits = |part: &str| !part.is_empty() && part.bytes().all(|byte| byte.is_ascii_digit());
    if !digits(major) || !digits(minor) {
        return None;
    }
    Some((
        major.parse().unwrap_or(u64::MAX),
        minor.parse().unwrap_or(u64::MAX),
    ))
}

/// Reads a record another process wrote: accepts current records and logquill 1.x ones.
///
/// A record with no `schema_version` is labelled `"1.0"` and has its camelCase `meta` keys
/// renamed; a missing `meta` becomes `{}`; unknown top-level keys are kept; a `null` `llm`
/// counts as absent. The input is not modified.
pub fn parse_record(raw: &Value) -> Result<Record, RecordError> {
    let Value::Object(object) = raw else {
        return Err(RecordError::NotAnObject {
            got: type_name(raw),
        });
    };

    let legacy = !object.contains_key("schema_version");
    let schema_version = match object.get("schema_version") {
        None => LEGACY_SCHEMA_VERSION.to_string(),
        Some(Value::String(version)) => version.clone(),
        Some(other) => {
            return Err(RecordError::BadSchemaVersion {
                got: other.to_string(),
            })
        }
    };
    let Some((major, _minor)) = parse_version(&schema_version) else {
        return Err(RecordError::BadSchemaVersion {
            got: Value::String(schema_version).to_string(),
        });
    };
    let supported_major = parse_version(SCHEMA_VERSION).map_or(2, |(major, _)| major);
    if major > supported_major {
        return Err(RecordError::NewerMajorVersion {
            version: schema_version,
        });
    }

    let text = |field: &'static str| match object.get(field) {
        Some(Value::String(text)) => Ok(text.clone()),
        other => Err(RecordError::FieldNotAString {
            field,
            got: other.map_or_else(|| "null".to_string(), Value::to_string),
        }),
    };
    let timestamp = text("timestamp")?;
    let level_name = text("level")?;
    let logger = text("logger")?;
    let message = text("message")?;
    let level = Level::from_record_name(&level_name)
        .ok_or(RecordError::UnknownLevel { got: level_name })?;

    let meta = match object.get("meta") {
        None => Meta::new(),
        Some(Value::Object(meta)) if legacy => rename_legacy_keys(meta),
        Some(Value::Object(meta)) => meta.clone(),
        Some(other) => {
            return Err(RecordError::MetaNotAnObject {
                got: type_name(other),
            })
        }
    };
    let llm = match object.get("llm") {
        None | Some(Value::Null) => None,
        Some(Value::Object(llm)) => Some(llm.clone()),
        Some(other) => {
            return Err(RecordError::LlmNotAnObject {
                got: type_name(other),
            })
        }
    };
    let extra = object
        .iter()
        .filter(|(key, _)| !KNOWN_KEYS.contains(&key.as_str()))
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();

    Ok(Record {
        schema_version,
        timestamp,
        level,
        logger,
        message,
        meta,
        llm,
        extra,
    })
}

fn rename_legacy_keys(meta: &Meta) -> Meta {
    meta.iter()
        .map(|(key, value)| {
            let renamed = LEGACY_META_KEYS
                .iter()
                .find(|(old, _)| old == key)
                .map_or(key.as_str(), |(_, new)| *new);
            (renamed.to_string(), value.clone())
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    struct Fixed(i64);

    impl Clock for Fixed {
        fn now_unix_micros(&self) -> i64 {
            self.0
        }
    }

    fn record() -> Value {
        json!({
            "schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.000Z",
            "level": "INFO", "logger": "app", "message": "hi", "meta": {"a": 1}
        })
    }

    #[test]
    fn a_new_record_is_stamped_and_ordered() {
        let clock = Fixed(1_700_000_000_123_456);
        let built = Record::new(&clock, Level::Warn, "app.api", "slow", Meta::new(), None);
        assert_eq!(built.schema_version, "2.0");
        assert_eq!(built.timestamp, "2023-11-14T22:13:20.123Z");
        let keys: Vec<_> = built
            .to_value()
            .as_object()
            .unwrap()
            .keys()
            .cloned()
            .collect();
        assert_eq!(
            keys,
            [
                "schema_version",
                "timestamp",
                "level",
                "logger",
                "message",
                "meta"
            ]
        );
    }

    #[test]
    fn a_current_record_reads_back_unchanged() {
        let raw = record();
        assert_eq!(parse_record(&raw).unwrap().to_value(), raw);
    }

    #[test]
    fn a_legacy_record_is_labelled_and_its_meta_keys_renamed() {
        let raw = json!({
            "timestamp": "t", "level": "INFO", "logger": "l", "message": "m",
            "meta": {"runId": "r", "other": 1}
        });
        let read = parse_record(&raw).unwrap();
        assert_eq!(read.schema_version, "1.0");
        assert_eq!(read.meta.get("run_id"), Some(&json!("r")));
        assert_eq!(read.meta.get("other"), Some(&json!(1)));
    }

    #[test]
    fn a_current_record_keeps_camel_case_meta_keys() {
        let mut raw = record();
        raw["meta"] = json!({"runId": "r"});
        assert_eq!(
            parse_record(&raw).unwrap().meta.get("runId"),
            Some(&json!("r"))
        );
    }

    #[test]
    fn unknown_keys_and_a_newer_minor_version_pass_through() {
        let mut raw = record();
        raw["schema_version"] = json!("2.7");
        raw["future"] = json!({"x": 1});
        let read = parse_record(&raw).unwrap();
        assert_eq!(read.extra.get("future"), Some(&json!({"x": 1})));
        assert_eq!(read.to_value(), raw);
    }

    #[test]
    fn a_null_llm_counts_as_absent() {
        let mut raw = record();
        raw["llm"] = Value::Null;
        assert_eq!(parse_record(&raw).unwrap().llm, None);
    }

    #[test]
    fn refusals_carry_the_shared_messages() {
        let cases = [
            (json!([1]), "a log record must be a JSON object, got array"),
            (
                {
                    let mut raw = record();
                    raw["schema_version"] = json!(2);
                    raw
                },
                "schema_version must be a string like '2.0', got 2 — is this a LogQuill record?",
            ),
            (
                {
                    let mut raw = record();
                    raw["schema_version"] = json!("3.0");
                    raw
                },
                "this record has schema_version \"3.0\", newer than the '2.0' this logquill understands — upgrade logquill to read it",
            ),
            (
                {
                    let mut raw = record();
                    raw["message"] = json!(5);
                    raw
                },
                "record field 'message' must be a string, got 5",
            ),
            (
                {
                    let mut raw = record();
                    raw["level"] = json!("info");
                    raw
                },
                "record level \"info\" isn't one of TRACE, DEBUG, INFO, WARN, ERROR, FATAL (upper case)",
            ),
            (
                {
                    let mut raw = record();
                    raw["meta"] = json!("x");
                    raw
                },
                "record field 'meta' must be an object, got string",
            ),
            (
                {
                    let mut raw = record();
                    raw["llm"] = json!("x");
                    raw
                },
                "record field 'llm' must be an object, got string",
            ),
        ];
        for (raw, message) in cases {
            assert_eq!(parse_record(&raw).unwrap_err().to_string(), message);
        }
    }

    #[test]
    fn a_missing_required_field_reads_as_null() {
        let mut raw = record();
        raw.as_object_mut().unwrap().remove("logger");
        assert_eq!(
            parse_record(&raw).unwrap_err().to_string(),
            "record field 'logger' must be a string, got null"
        );
    }

    #[test]
    fn a_version_part_too_long_for_u64_is_newer() {
        let mut raw = record();
        raw["schema_version"] = json!("99999999999999999999999.0");
        assert!(matches!(
            parse_record(&raw),
            Err(RecordError::NewerMajorVersion { .. })
        ));
    }
}
