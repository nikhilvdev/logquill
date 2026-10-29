//! The core's remaining pure logic for the browser: level parsing, the `llm` block, trace and OpenTelemetry
//! ids, the GenAI attribute mapping (`semconv`), trace trees, the run-id stamp and alert de-duplication.
//! Mirrors `bindings/node/src/derive.rs`; records cross as JSON text, and so do structured results.

use std::sync::Mutex;

use logquill_core as core;
use serde_json::{json, Map, Value};
use wasm_bindgen::prelude::*;

use crate::{fail, locked, object_of, parse, Options};

// ---- levels, the llm block ------------------------------------------------------------------

/// The canonical level name for a level given as JSON text (a name in any case, or a weight).
#[wasm_bindgen(js_name = levelFromJson)]
pub fn level_from_json(value: &str) -> Result<String, JsError> {
    core::Level::from_json(&parse(value, "the level")?)
        .map(|level| level.name().to_string())
        .map_err(|e| fail(e.to_string()))
}

/// An `llm` block built from named fields (JSON text of an object). JSON text: `{block, warnings}` where
/// `block` is the block as JSON text, or `null` when nothing valid was left.
#[wasm_bindgen(js_name = buildLlmBlockJson)]
pub fn build_llm_block_json(fields: &str) -> Result<String, JsError> {
    let built = core::build_llm_block(&object_of(fields, "the fields")?);
    Ok(json!({
        "block": built.block.map(|block| Value::Object(block).to_string()),
        "warnings": built.warnings,
    })
    .to_string())
}

// ---- trace and OpenTelemetry ids ------------------------------------------------------------

/// The trace id carried by a W3C `traceparent`, AWS X-Ray or GCP trace header, if it has one.
#[wasm_bindgen(js_name = parseTraceHeader)]
pub fn parse_trace_header(header: &str) -> Option<String> {
    core::parse_trace_header(header)
}

/// The OpenTelemetry span id (16 hex characters) for a LogQuill span id.
#[wasm_bindgen(js_name = spanIdFor)]
pub fn span_id_for(value: &str) -> String {
    core::span_id_for(value)
}

/// The OpenTelemetry trace id (32 hex characters) for a record's `meta` (JSON text), if it has one.
#[wasm_bindgen(js_name = traceIdFor)]
pub fn trace_id_for(meta: &str) -> Result<Option<String>, JsError> {
    Ok(core::trace_id_for(&object_of(meta, "the meta")?))
}

/// The trace id for a span whose record names only a parent.
#[wasm_bindgen(js_name = traceIdFromParent)]
pub fn trace_id_from_parent(parent_span_id: &str) -> String {
    core::trace_id_from_parent(parent_span_id)
}

// ---- GenAI attribute mapping ----------------------------------------------------------------

/// The environment variable that turns on prompt and completion capture.
#[wasm_bindgen(js_name = captureEnv)]
pub fn capture_env() -> String {
    core::CAPTURE_ENV.to_string()
}

fn convention(name: Option<&str>) -> Result<core::Convention, JsError> {
    core::resolve_convention(name).map_err(fail)
}

/// The convention in force for `explicit` (or the default): JSON text `{version, names}`.
#[wasm_bindgen(js_name = semconvResolve)]
pub fn semconv_resolve(explicit: Option<String>) -> Result<String, JsError> {
    let convention = convention(explicit.as_deref())?;
    let names: Map<String, Value> = convention
        .names()
        .into_iter()
        .map(|(key, value)| (key.to_string(), json!(value)))
        .collect();
    Ok(json!({"version": convention.version(), "names": names}).to_string())
}

/// Whether the capture variable's value turns content capture on.
#[wasm_bindgen(js_name = captureContentEnabled)]
pub fn capture_content_enabled(value: Option<String>) -> bool {
    core::capture_content_enabled(value.as_deref())
}

/// A record timestamp as nanoseconds since the epoch, as a decimal string, if it parses.
#[wasm_bindgen(js_name = epochNs)]
pub fn epoch_ns(timestamp: &str) -> Option<String> {
    core::epoch_ns(timestamp).map(|ns| ns.to_string())
}

/// What kind of exportable record this is: JSON text `{span, llm, tool}`.
#[wasm_bindgen(js_name = classifyRecordJson)]
pub fn classify_record_json(record: &str) -> Result<String, JsError> {
    let record = parse(record, "the record")?;
    Ok(json!({
        "span": core::is_span_record(&record),
        "llm": core::is_llm_record(&record),
        "tool": core::is_tool_record(&record),
    })
    .to_string())
}

/// The OpenTelemetry log attributes for a record (JSON text), as JSON text of an object.
#[wasm_bindgen(js_name = logAttributes)]
pub fn log_attributes(record: &str, convention_name: Option<String>) -> Result<String, JsError> {
    let convention = convention(Some(convention_name.as_deref().unwrap_or("latest")))?;
    Ok(Value::Object(core::log_attributes(
        &parse(record, "the record")?,
        convention,
    ))
    .to_string())
}

/// The OpenTelemetry span a record exports as (JSON text), or nothing when it is not a span, tool or LLM call.
#[wasm_bindgen(js_name = exportSpec)]
pub fn export_spec(
    record: &str,
    convention_name: Option<String>,
    default_provider: Option<String>,
    capture_content: Option<bool>,
) -> Result<Option<String>, JsError> {
    let convention = convention(Some(convention_name.as_deref().unwrap_or("latest")))?;
    let defaults = core::SpecOptions::default();
    let options = core::SpecOptions {
        default_provider: default_provider.unwrap_or(defaults.default_provider),
        capture_content: capture_content.unwrap_or(false),
    };
    Ok(
        core::export_spec(&parse(record, "the record")?, convention, &options)
            .map(|spec| spec.to_value().to_string()),
    )
}

// ---- trace trees ----------------------------------------------------------------------------

/// The span tree of one run, from records (JSON text of an array): `{record_count, roots, text}` as JSON text.
#[wasm_bindgen(js_name = buildTrace)]
pub fn build_trace(records: &str, run_id: &str) -> Result<String, JsError> {
    let Value::Array(items) = parse(records, "the records")? else {
        return Err(fail("the records must be a JSON array of record objects"));
    };
    let trace = core::build_trace(items.iter(), run_id);
    Ok(json!({
        "record_count": trace.record_count,
        "roots": trace.roots.iter().map(core::TreeNode::to_value).collect::<Vec<_>>(),
        "text": core::render_tree(&trace.roots),
    })
    .to_string())
}

// ---- run stamp ------------------------------------------------------------------------------

/// Stamps `meta.run_id` and a step counter on each record of a run.
#[wasm_bindgen(js_name = RunStamp)]
pub struct RunStamp {
    inner: Mutex<core::RunStamp>,
}

#[wasm_bindgen(js_class = RunStamp)]
impl RunStamp {
    /// `runId` is the id to use, if the caller has one; `generated` is the id to use when it does not (the
    /// SDK passes a fresh UUID, because the core has no random source of its own).
    #[wasm_bindgen(constructor)]
    pub fn new(run_id: Option<String>, generated: String) -> RunStamp {
        RunStamp {
            inner: Mutex::new(core::RunStamp::new(run_id.as_deref(), || generated)),
        }
    }

    /// `meta` (JSON text) with `run_id` (if absent) and the next `step` added, as JSON text.
    pub fn stamp(&self, meta: &str) -> Result<String, JsError> {
        let mut map = object_of(meta, "the meta")?;
        locked(&self.inner).stamp(&mut map);
        Ok(Value::Object(map).to_string())
    }

    pub fn steps(&self) -> f64 {
        locked(&self.inner).steps() as f64
    }

    #[wasm_bindgen(js_name = runId)]
    pub fn run_id(&self) -> String {
        locked(&self.inner).run_id().to_string()
    }
}

// ---- alert de-duplication -------------------------------------------------------------------

/// Alerts as JSON text: `[{message, occurrences, record}, ...]` (`record` is JSON text).
fn alerts_json(list: Vec<core::Alert>) -> String {
    Value::Array(
        list.into_iter()
            .map(|a| {
                json!({
                    "message": a.message,
                    "occurrences": a.occurrences as f64,
                    "record": a.record.to_string(),
                })
            })
            .collect(),
    )
    .to_string()
}

/// Collapses repeats of one error inside a time window into a single alert with a count.
#[wasm_bindgen(js_name = AlertDedupe)]
pub struct AlertDedupe {
    inner: Mutex<core::AlertDedupe>,
}

#[wasm_bindgen(js_class = AlertDedupe)]
impl AlertDedupe {
    /// `options`: `{threshold?, dedupeWindowSeconds?, maxTrackedKeys?, key?}` as JSON text.
    #[wasm_bindgen(constructor)]
    pub fn new(options: Option<String>) -> Result<AlertDedupe, JsError> {
        let defaults = core::AlertOptions::default();
        let o = Options::from_json(options)?;
        let threshold = match o.string("threshold") {
            None => defaults.threshold,
            Some(name) => core::Level::from_record_name(&name.to_ascii_uppercase())
                .ok_or_else(|| fail(format!("Unknown log level: {name}")))?,
        };
        Ok(AlertDedupe {
            inner: Mutex::new(core::AlertDedupe::new(core::AlertOptions {
                threshold,
                window_seconds: o
                    .f64("dedupeWindowSeconds")
                    .unwrap_or(defaults.window_seconds),
                max_tracked_keys: o
                    .usize("maxTrackedKeys")
                    .unwrap_or(defaults.max_tracked_keys),
                by_message: o.string("key").as_deref() == Some("message"),
            })),
        })
    }

    /// Feeds one record (JSON text) at time `at` (seconds); JSON text of the alerts due now.
    pub fn observe(&self, record: &str, at: f64) -> Result<String, JsError> {
        let record = parse(record, "the record")?;
        Ok(alerts_json(locked(&self.inner).observe(&record, at)))
    }

    /// Moves the clock to `at` and returns the alerts whose window ended (JSON text).
    pub fn advance(&self, at: f64) -> String {
        alerts_json(locked(&self.inner).advance(at))
    }

    /// Drops every tracked key without sending anything more.
    pub fn close(&self) {
        locked(&self.inner).close();
    }

    pub fn tracked(&self) -> u32 {
        u32::try_from(locked(&self.inner).tracked()).unwrap_or(u32::MAX)
    }
}
