//! The core's stateful plugin logic for the browser: sampling with tail elevation, rate limiting, adaptive
//! sampling, the flight recorder and the run summary. Mirrors `bindings/node/src/state.rs`. The clock and
//! the random draw are arguments, never read here, so a test steps them and the browser SDK passes
//! `Date.now()` and `Math.random()`. Options arrive as JSON text; structured results leave as JSON text.

use std::sync::Mutex;

use logquill_core as core;
use logquill_core::Level;
use serde_json::{json, Value};
use wasm_bindgen::prelude::*;

use crate::{fail, locked, parse, Options};

fn level(name: &str) -> Result<Level, JsError> {
    Level::from_record_name(name).ok_or_else(|| fail(format!("Unknown log level: {name}")))
}

/// A sampling decision as JSON text: `{kept, released: [<record as JSON text>, ...]}`.
fn decision(decision: core::Decision) -> String {
    json!({
        "kept": decision.kept,
        "released": decision.released.iter().map(Value::to_string).collect::<Vec<_>>(),
    })
    .to_string()
}

/// Head-based sampling at a fixed rate, with tail elevation by trace.
#[wasm_bindgen(js_name = Sampling)]
pub struct Sampling {
    inner: Mutex<core::Sampling>,
}

#[wasm_bindgen(js_class = Sampling)]
impl Sampling {
    /// `options`: `{rate, tail?, traceKey?, elevateAt?, maxBufferedRecords?, maxTraces?, maxElevated?}` as JSON text.
    #[wasm_bindgen(constructor)]
    pub fn new(options: &str) -> Result<Sampling, JsError> {
        let o = Options::from_json(Some(options.to_string()))?;
        let options = core::SamplingOptions {
            rate: o.f64("rate").unwrap_or(0.0),
            tail: o.bool("tail").unwrap_or(false),
            trace_key: o
                .string("traceKey")
                .unwrap_or_else(|| "trace_id".to_string()),
            elevate_at: level(o.string("elevateAt").as_deref().unwrap_or("ERROR"))?,
            max_buffered_records: o.usize("maxBufferedRecords").unwrap_or(1000),
            max_traces: o.usize("maxTraces").unwrap_or(200),
            max_elevated: o.usize("maxElevated").unwrap_or(10_000),
        };
        Ok(Sampling {
            inner: Mutex::new(core::Sampling::new(options).map_err(fail)?),
        })
    }

    /// Decides one record (JSON text) given a random draw in [0, 1). JSON text: `{kept, released}`.
    pub fn decide(&self, record: &str, draw: f64) -> Result<String, JsError> {
        let value = parse(record, "the record")?;
        Ok(decision(locked(&self.inner).decide(&value, draw)))
    }

    #[wasm_bindgen(js_name = bufferedRecords)]
    pub fn buffered_records(&self) -> u32 {
        u32::try_from(locked(&self.inner).buffered_records()).unwrap_or(u32::MAX)
    }

    #[wasm_bindgen(js_name = bufferedTraces)]
    pub fn buffered_traces(&self) -> String {
        Value::Array(locked(&self.inner).buffered_traces()).to_string()
    }

    pub fn elevated(&self) -> String {
        Value::Array(locked(&self.inner).elevated()).to_string()
    }
}

/// Drops records once a stream exceeds `maxRecords` inside its own rolling window.
#[wasm_bindgen(js_name = RateLimit)]
pub struct RateLimit {
    inner: Mutex<core::RateLimit>,
}

#[wasm_bindgen(js_class = RateLimit)]
impl RateLimit {
    /// `options`: `{maxRecords, perSeconds, maxKeys?, key?}` as JSON text.
    #[wasm_bindgen(constructor)]
    pub fn new(options: &str) -> Result<RateLimit, JsError> {
        let o = Options::from_json(Some(options.to_string()))?;
        let key = match o.string("key").as_deref().unwrap_or("logger_level") {
            "logger_level" => core::RateKey::LoggerLevel,
            "logger" => core::RateKey::Logger,
            "message" => core::RateKey::Message,
            other => {
                return Err(fail(format!(
                    "key must be 'logger_level', 'logger' or 'message', got '{other}'"
                )))
            }
        };
        Ok(RateLimit {
            inner: Mutex::new(
                core::RateLimit::new(
                    o.f64("maxRecords").unwrap_or(0.0).max(0.0) as u64,
                    o.f64("perSeconds").unwrap_or(1.0),
                    o.usize("maxKeys").unwrap_or(1000),
                    key,
                )
                .map_err(fail)?,
            ),
        })
    }

    /// Whether `record` (JSON text) is within its limit at `now` (seconds).
    pub fn allow(&self, record: &str, now: f64) -> Result<bool, JsError> {
        let value = parse(record, "the record")?;
        Ok(locked(&self.inner).allow(&value, now))
    }

    /// The open windows as JSON text: `[[key, start, count], ...]`.
    pub fn windows(&self) -> String {
        Value::Array(
            locked(&self.inner)
                .windows()
                .into_iter()
                .map(|(key, start, count)| json!([key, start, count]))
                .collect(),
        )
        .to_string()
    }
}

/// Keeps errors and slow spans, samples the rest at a rate that follows demand, and caps bytes per window.
#[wasm_bindgen(js_name = AdaptiveSampling)]
pub struct AdaptiveSampling {
    inner: Mutex<core::AdaptiveSampling>,
}

#[wasm_bindgen(js_class = AdaptiveSampling)]
impl AdaptiveSampling {
    /// `now` is the clock reading (seconds) the first window starts from. `options`: `{baseRate?, minRate?,
    /// maxRate?, keepAt?, slowMs?, maxBytesPerSecond?, windowSeconds?}` as JSON text.
    #[wasm_bindgen(constructor)]
    pub fn new(now: f64, options: Option<String>) -> Result<AdaptiveSampling, JsError> {
        let o = Options::from_json(options)?;
        let options = core::AdaptiveOptions {
            base_rate: o.f64("baseRate").unwrap_or(0.1),
            min_rate: o.f64("minRate").unwrap_or(0.01),
            max_rate: o.f64("maxRate").unwrap_or(1.0),
            keep_at: level(o.string("keepAt").as_deref().unwrap_or("ERROR"))?,
            slow_ms: o.f64("slowMs").unwrap_or(1000.0),
            max_bytes_per_second: o
                .f64("maxBytesPerSecond")
                .map_or(100_000, |n| n.max(0.0) as u64),
            window_seconds: o.f64("windowSeconds").unwrap_or(1.0),
        };
        Ok(AdaptiveSampling {
            inner: Mutex::new(core::AdaptiveSampling::new(options, now).map_err(fail)?),
        })
    }

    /// Whether to keep `record` (JSON text) at clock reading `now` (seconds), given a draw in [0, 1).
    pub fn decide(&self, record: &str, now: f64, draw: f64) -> Result<bool, JsError> {
        let value = parse(record, "the record")?;
        Ok(locked(&self.inner).decide(&value, now, draw))
    }

    /// The sample rate in force now.
    #[wasm_bindgen(getter)]
    pub fn rate(&self) -> f64 {
        locked(&self.inner).rate()
    }

    /// Bytes this window would have written (`demand`) and did write (`emitted`), as JSON text.
    #[wasm_bindgen(js_name = windowBytesJson)]
    pub fn window_bytes_json(&self) -> String {
        let (demand, emitted) = locked(&self.inner).window_bytes();
        json!({"demand": demand as f64, "emitted": emitted as f64}).to_string()
    }
}

/// A bounded per-run ring buffer of low-level records that ships only if the run errors.
#[wasm_bindgen(js_name = FlightRecorder)]
pub struct FlightRecorder {
    inner: Mutex<core::FlightRecorder>,
}

#[wasm_bindgen(js_class = FlightRecorder)]
impl FlightRecorder {
    /// `options`: `{runKey?, shipAt?, flushAt?, maxBufferedRecords?, maxRuns?, maxFlushed?}` as JSON text.
    #[wasm_bindgen(constructor)]
    pub fn new(options: Option<String>) -> Result<FlightRecorder, JsError> {
        let o = Options::from_json(options)?;
        let options = core::FlightOptions {
            run_key: o.string("runKey").unwrap_or_else(|| "run_id".to_string()),
            ship_at: level(o.string("shipAt").as_deref().unwrap_or("INFO"))?,
            flush_at: level(o.string("flushAt").as_deref().unwrap_or("ERROR"))?,
            max_buffered_records: o.usize("maxBufferedRecords").unwrap_or(1000),
            max_runs: o.usize("maxRuns").unwrap_or(200),
            max_flushed: o.usize("maxFlushed").unwrap_or(10_000),
        };
        Ok(FlightRecorder {
            inner: Mutex::new(core::FlightRecorder::new(options)),
        })
    }

    /// Decides one record (JSON text). JSON text: `{kept, released}`.
    pub fn decide(&self, record: &str) -> Result<String, JsError> {
        let value = parse(record, "the record")?;
        Ok(decision(locked(&self.inner).decide(&value)))
    }

    #[wasm_bindgen(js_name = bufferedRecords)]
    pub fn buffered_records(&self) -> u32 {
        u32::try_from(locked(&self.inner).buffered_records()).unwrap_or(u32::MAX)
    }

    #[wasm_bindgen(js_name = bufferedRuns)]
    pub fn buffered_runs(&self) -> String {
        Value::Array(locked(&self.inner).buffered_runs()).to_string()
    }

    pub fn flushed(&self) -> String {
        Value::Array(locked(&self.inner).flushed()).to_string()
    }
}

/// One record per run on close, with total tokens, cost, tool calls, retries and errors.
#[wasm_bindgen(js_name = RunSummary)]
pub struct RunSummary {
    inner: Mutex<core::RunSummary>,
}

#[wasm_bindgen(js_class = RunSummary)]
impl RunSummary {
    /// `options`: `{runKey?, loggerName?, maxRuns?}` as JSON text.
    #[wasm_bindgen(constructor)]
    pub fn new(options: Option<String>) -> Result<RunSummary, JsError> {
        let o = Options::from_json(options)?;
        Ok(RunSummary {
            inner: Mutex::new(core::RunSummary::new(core::SummaryOptions {
                run_key: o.string("runKey").unwrap_or_else(|| "run_id".to_string()),
                logger_name: o
                    .string("loggerName")
                    .unwrap_or_else(|| "app.run_summary".to_string()),
                max_runs: o.usize("maxRuns").unwrap_or(1000),
            })),
        })
    }

    /// Feeds one record (JSON text). Returns the summary record (JSON text) when the run closed, else nothing.
    pub fn observe(&self, record: &str, timestamp: &str) -> Result<Option<String>, JsError> {
        let value = parse(record, "the record")?;
        Ok(locked(&self.inner)
            .observe(&value, timestamp)
            .map(|summary| summary.to_string()))
    }

    #[wasm_bindgen(js_name = openRuns)]
    pub fn open_runs(&self) -> String {
        Value::Array(locked(&self.inner).open_runs()).to_string()
    }
}
