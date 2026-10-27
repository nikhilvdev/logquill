//! The core's remaining pure logic for Node: level parsing, record timestamps and the `llm` block,
//! trace and OpenTelemetry ids, the GenAI attribute mapping (`semconv`), trace trees, the run-id
//! stamp and alert de-duplication. Records cross as JSON text, like the rest of the addon.

use std::sync::Mutex;

use logquill_core as core;
use napi::{Error, Result};
use napi_derive::napi;
use serde_json::{json, Map, Value};

use crate::parse;

fn reason(message: impl Into<String>) -> Error {
    Error::from_reason(message.into())
}

fn object_of(text: &str, what: &str) -> Result<Map<String, Value>> {
    match parse(text, what)? {
        Value::Object(map) => Ok(map),
        _ => Err(reason(format!("{what} must be a JSON object"))),
    }
}

fn locked<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|p| p.into_inner())
}

// ---- levels, timestamps, the llm block ------------------------------------------------------

/// The canonical level name for a level given as JSON text (a name in any case, or a weight).
/// Throws with the contract's message for anything else.
#[napi]
pub fn level_from_json(value: String) -> Result<String> {
    core::Level::from_json(&parse(&value, "the level")?)
        .map(|level| level.name().to_string())
        .map_err(|e| reason(e.to_string()))
}

/// A record timestamp for `unixMicros` microseconds since the epoch: `YYYY-MM-DDTHH:MM:SS.mmmZ`.
#[napi]
pub fn format_timestamp(unix_micros: f64) -> String {
    core::format_timestamp(unix_micros as i64)
}

/// An `llm` block built from named fields, and what was wrong with the ones that were dropped.
#[napi(object)]
pub struct LlmBuilt {
    /// The block as JSON text, or `null` when nothing valid was left.
    pub block: Option<String>,
    pub warnings: Vec<String>,
}

/// Builds an `llm` block from named fields (JSON text of an object).
#[napi]
pub fn build_llm_block(fields: String) -> Result<LlmBuilt> {
    let built = core::build_llm_block(&object_of(&fields, "the fields")?);
    Ok(LlmBuilt {
        block: built.block.map(|block| Value::Object(block).to_string()),
        warnings: built.warnings,
    })
}

// ---- trace and OpenTelemetry ids ------------------------------------------------------------

/// The trace id carried by a W3C `traceparent`, AWS X-Ray or GCP trace header, if it has one.
#[napi]
pub fn parse_trace_header(header: String) -> Option<String> {
    core::parse_trace_header(&header)
}

/// The OpenTelemetry span id (16 hex digits) for a LogQuill span id.
#[napi]
pub fn span_id_for(value: String) -> String {
    core::span_id_for(&value)
}

/// The OpenTelemetry trace id (32 hex digits) for a record's `meta` (JSON text), if it has the ids to derive one.
#[napi]
pub fn trace_id_for(meta: String) -> Result<Option<String>> {
    Ok(core::trace_id_for(&object_of(&meta, "the meta")?))
}

/// The trace id derived from a parent span id when the record has neither a trace nor a run id.
#[napi]
pub fn trace_id_from_parent(parent_span_id: String) -> String {
    core::trace_id_from_parent(&parent_span_id)
}

// ---- the GenAI attribute mapping ------------------------------------------------------------

/// The environment variable that turns prompt and completion capture on.
#[napi(js_name = "CAPTURE_ENV")]
pub const CAPTURE_ENV: &str = core::CAPTURE_ENV;

fn convention(name: Option<&str>) -> Result<core::Convention> {
    core::resolve_convention(name).map_err(reason)
}

/// The attribute names of a convention (`"latest"` or `"legacy"`; unset reads `OTEL_SEMCONV_STABILITY_OPT_IN`
/// the way the core does), as JSON text `{ version, names }`. Throws on an unknown convention.
#[napi]
pub fn semconv_resolve(explicit: Option<String>) -> Result<String> {
    let convention = convention(explicit.as_deref())?;
    let names: Map<String, Value> = convention
        .names()
        .into_iter()
        .map(|(key, value)| (key.to_string(), json!(value)))
        .collect();
    Ok(json!({"version": convention.version(), "names": names}).to_string())
}

/// Whether the capture environment variable's value switches content capture on.
#[napi]
pub fn capture_content_enabled(value: Option<String>) -> bool {
    core::capture_content_enabled(value.as_deref())
}

/// Nanoseconds since the epoch for a record timestamp, as a decimal string (it can exceed 2^53),
/// or `null` if the timestamp cannot be read.
#[napi]
pub fn epoch_ns(timestamp: String) -> Option<String> {
    core::epoch_ns(&timestamp).map(|ns| ns.to_string())
}

/// What kind of exportable record a record is.
#[napi(object)]
pub struct Classification {
    pub span: bool,
    pub llm: bool,
    pub tool: bool,
}

#[napi]
pub fn classify_record(record: String) -> Result<Classification> {
    let record = parse(&record, "the record")?;
    Ok(Classification {
        span: core::is_span_record(&record),
        llm: core::is_llm_record(&record),
        tool: core::is_tool_record(&record),
    })
}

/// The GenAI attributes for a log record (its `llm` block's usage and model), as JSON text.
#[napi]
pub fn log_attributes(record: String, convention_name: Option<String>) -> Result<String> {
    let convention = convention(Some(convention_name.as_deref().unwrap_or("latest")))?;
    Ok(Value::Object(core::log_attributes(
        &parse(&record, "the record")?,
        convention,
    ))
    .to_string())
}

/// The span a record becomes when exported (LLM, then tool, then span), as JSON text, or `null`
/// for any other record.
#[napi]
pub fn export_spec(
    record: String,
    convention_name: Option<String>,
    default_provider: Option<String>,
    capture_content: Option<bool>,
) -> Result<Option<String>> {
    let convention = convention(Some(convention_name.as_deref().unwrap_or("latest")))?;
    let defaults = core::SpecOptions::default();
    let options = core::SpecOptions {
        default_provider: default_provider.unwrap_or(defaults.default_provider),
        capture_content: capture_content.unwrap_or(false),
    };
    Ok(
        core::export_spec(&parse(&record, "the record")?, convention, &options)
            .map(|spec| spec.to_value().to_string()),
    )
}

// ---- trace trees ----------------------------------------------------------------------------

/// Rebuilds one run's span tree from its records (JSON text of an array): `{ record_count, roots, text }`
/// as JSON text, where `text` is the indented rendering.
#[napi]
pub fn build_trace(records: String, run_id: String) -> Result<String> {
    let Value::Array(items) = parse(&records, "the records")? else {
        return Err(reason("the records must be a JSON array of record objects"));
    };
    let trace = core::build_trace(items.iter(), &run_id);
    Ok(json!({
        "record_count": trace.record_count,
        "roots": trace.roots.iter().map(core::TreeNode::to_value).collect::<Vec<_>>(),
        "text": core::render_tree(&trace.roots),
    })
    .to_string())
}

// ---- run stamp and alert de-duplication -----------------------------------------------------

/// Stamps `meta.run_id` and a step counter on each record of a run.
#[napi]
pub struct RunStamp {
    inner: Mutex<core::RunStamp>,
}

#[napi]
impl RunStamp {
    /// Uses `runId` unless it is missing or empty, in which case `generated` (a fresh id the
    /// caller made: the core has no random source) is used.
    #[napi(constructor)]
    pub fn new(run_id: Option<String>, generated: String) -> RunStamp {
        RunStamp {
            inner: Mutex::new(core::RunStamp::new(run_id.as_deref(), || generated)),
        }
    }

    /// `meta` (JSON text) with `run_id` (if absent) and the next `step` added.
    #[napi]
    pub fn stamp(&self, meta: String) -> Result<String> {
        let mut map = object_of(&meta, "the meta")?;
        locked(&self.inner).stamp(&mut map);
        Ok(Value::Object(map).to_string())
    }

    /// How many records have been stamped.
    #[napi]
    pub fn steps(&self) -> f64 {
        locked(&self.inner).steps() as f64
    }

    #[napi]
    pub fn run_id(&self) -> String {
        locked(&self.inner).run_id().to_string()
    }
}

/// Options for `AlertDedupe`; every field is optional.
#[napi(object)]
pub struct AlertConfig {
    /// The level that raises an alert (default `ERROR`).
    pub threshold: Option<String>,
    pub dedupe_window_seconds: Option<f64>,
    pub max_tracked_keys: Option<u32>,
    /// `"message"` collapses by message alone; anything else by level, logger and message.
    pub key: Option<String>,
}

/// One alert to send.
#[napi(object)]
pub struct AlertOut {
    pub message: String,
    /// 1 for the first occurrence; a summary of repeats carries the count.
    pub occurrences: f64,
    /// The window's first record, as JSON text, for a sender to format the alert from.
    pub record: String,
}

fn alerts(list: Vec<core::Alert>) -> Vec<AlertOut> {
    list.into_iter()
        .map(|a| AlertOut {
            message: a.message,
            occurrences: a.occurrences as f64,
            record: a.record.to_string(),
        })
        .collect()
}

/// Collapses repeated identical errors inside a window into one alert plus a count.
#[napi]
pub struct AlertDedupe {
    inner: Mutex<core::AlertDedupe>,
}

#[napi]
impl AlertDedupe {
    #[napi(constructor)]
    pub fn new(config: Option<AlertConfig>) -> Result<AlertDedupe> {
        let defaults = core::AlertOptions::default();
        let c = config.unwrap_or(AlertConfig {
            threshold: None,
            dedupe_window_seconds: None,
            max_tracked_keys: None,
            key: None,
        });
        let threshold = match c.threshold.as_deref() {
            None => defaults.threshold,
            Some(name) => core::Level::from_record_name(&name.to_ascii_uppercase())
                .ok_or_else(|| reason(format!("Unknown log level: {name}")))?,
        };
        Ok(AlertDedupe {
            inner: Mutex::new(core::AlertDedupe::new(core::AlertOptions {
                threshold,
                window_seconds: c.dedupe_window_seconds.unwrap_or(defaults.window_seconds),
                max_tracked_keys: c
                    .max_tracked_keys
                    .map_or(defaults.max_tracked_keys, |n| n as usize),
                by_message: c.key.as_deref() == Some("message"),
            })),
        })
    }

    /// Takes in a record at clock reading `at` (seconds); returns the alerts to send now.
    #[napi]
    pub fn observe(&self, record: String, at: f64) -> Result<Vec<AlertOut>> {
        let record = parse(&record, "the record")?;
        Ok(alerts(locked(&self.inner).observe(&record, at)))
    }

    /// Moves the clock to `at` without a record; returns the summaries now due.
    #[napi]
    pub fn advance(&self, at: f64) -> Vec<AlertOut> {
        alerts(locked(&self.inner).advance(at))
    }

    /// Stops tracking: nothing more is sent.
    #[napi]
    pub fn close(&self) {
        locked(&self.inner).close();
    }

    /// How many distinct errors are tracked.
    #[napi]
    pub fn tracked(&self) -> u32 {
        u32::try_from(locked(&self.inner).tracked()).unwrap_or(u32::MAX)
    }
}
