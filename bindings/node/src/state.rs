//! The core's stateful plugin logic for Node: sampling, rate limiting, adaptive sampling, the flight
//! recorder and the run summary. Each holds its state behind a lock. The clock and the random draw
//! are arguments, so the shared corpus can step each one deterministically; the SDK supplies real
//! ones. Records cross as JSON text.

use std::sync::Mutex;

use logquill_core as core;
use logquill_core::Level;
use napi::{Error, Result};
use napi_derive::napi;
use serde_json::Value;

use crate::parse;

fn reason(message: impl Into<String>) -> Error {
    Error::from_reason(message.into())
}

fn level(name: &str) -> Result<Level> {
    Level::from_record_name(name).ok_or_else(|| reason(format!("Unknown log level: {name}")))
}

fn locked<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|p| p.into_inner())
}

/// What a plugin that can hold records back decided about one record.
#[napi(object)]
pub struct Decision {
    /// Whether the record continues down the pipeline.
    pub kept: bool,
    /// Earlier records (JSON text) this one frees, in order.
    pub released: Vec<String>,
}

fn decision(decision: core::Decision) -> Decision {
    Decision {
        kept: decision.kept,
        released: decision.released.iter().map(Value::to_string).collect(),
    }
}

/// Options for `Sampling`; every field but `rate` is optional.
#[napi(object)]
pub struct SamplingConfig {
    pub rate: f64,
    /// Buffer a sampled-out trace and release it if it later errors.
    pub tail: Option<bool>,
    pub trace_key: Option<String>,
    pub elevate_at: Option<String>,
    pub max_buffered_records: Option<u32>,
    pub max_traces: Option<u32>,
    pub max_elevated: Option<u32>,
}

/// Keeps roughly `rate` of records; with `tail`, a sampled-out trace is buffered and released if it
/// later errors.
#[napi]
pub struct Sampling {
    inner: Mutex<core::Sampling>,
}

#[napi]
impl Sampling {
    #[napi(constructor)]
    pub fn new(config: SamplingConfig) -> Result<Sampling> {
        let options = core::SamplingOptions {
            rate: config.rate,
            tail: config.tail.unwrap_or(false),
            trace_key: config.trace_key.unwrap_or_else(|| "trace_id".to_string()),
            elevate_at: level(config.elevate_at.as_deref().unwrap_or("ERROR"))?,
            max_buffered_records: config.max_buffered_records.map_or(1000, |n| n as usize),
            max_traces: config.max_traces.map_or(200, |n| n as usize),
            max_elevated: config.max_elevated.map_or(10_000, |n| n as usize),
        };
        Ok(Sampling {
            inner: Mutex::new(core::Sampling::new(options).map_err(reason)?),
        })
    }

    /// Decides one record given a random draw in `[0, 1)`.
    #[napi]
    pub fn decide(&self, record: String, draw: f64) -> Result<Decision> {
        let value = parse(&record, "the record")?;
        Ok(decision(locked(&self.inner).decide(&value, draw)))
    }

    /// How many records are buffered waiting on their trace.
    #[napi]
    pub fn buffered_records(&self) -> u32 {
        u32::try_from(locked(&self.inner).buffered_records()).unwrap_or(u32::MAX)
    }

    /// The traces holding buffered records, oldest first, as JSON text of an array.
    #[napi]
    pub fn buffered_traces(&self) -> String {
        Value::Array(locked(&self.inner).buffered_traces()).to_string()
    }

    /// The traces already elevated by an error, as JSON text of an array.
    #[napi]
    pub fn elevated(&self) -> String {
        Value::Array(locked(&self.inner).elevated()).to_string()
    }
}

/// Options for `RateLimit`.
#[napi(object)]
pub struct RateLimitConfig {
    pub max_records: f64,
    pub per_seconds: f64,
    pub max_keys: Option<u32>,
    /// `"logger_level"` (default), `"logger"` or `"message"`.
    pub key: Option<String>,
}

/// Drops records once a stream exceeds `max_records` inside its own rolling window.
#[napi]
pub struct RateLimit {
    inner: Mutex<core::RateLimit>,
}

#[napi]
impl RateLimit {
    #[napi(constructor)]
    pub fn new(config: RateLimitConfig) -> Result<RateLimit> {
        let key = match config.key.as_deref().unwrap_or("logger_level") {
            "logger_level" => core::RateKey::LoggerLevel,
            "logger" => core::RateKey::Logger,
            "message" => core::RateKey::Message,
            other => {
                return Err(reason(format!(
                    "key must be 'logger_level', 'logger' or 'message', got '{other}'"
                )))
            }
        };
        Ok(RateLimit {
            inner: Mutex::new(
                core::RateLimit::new(
                    config.max_records.max(0.0) as u64,
                    config.per_seconds,
                    config.max_keys.map_or(1000, |n| n as usize),
                    key,
                )
                .map_err(reason)?,
            ),
        })
    }

    /// Whether `record` may pass at clock reading `now` (seconds).
    #[napi]
    pub fn allow(&self, record: String, now: f64) -> Result<bool> {
        let value = parse(&record, "the record")?;
        Ok(locked(&self.inner).allow(&value, now))
    }

    /// The open windows as JSON text: `[[key, start, count], ...]`.
    #[napi]
    pub fn windows(&self) -> String {
        Value::Array(
            locked(&self.inner)
                .windows()
                .into_iter()
                .map(|(key, start, count)| serde_json::json!([key, start, count]))
                .collect(),
        )
        .to_string()
    }
}

/// Options for `AdaptiveSampling`; every field is optional.
#[napi(object)]
pub struct AdaptiveConfig {
    pub base_rate: Option<f64>,
    pub min_rate: Option<f64>,
    pub max_rate: Option<f64>,
    pub keep_at: Option<String>,
    pub slow_ms: Option<f64>,
    pub max_bytes_per_second: Option<f64>,
    pub window_seconds: Option<f64>,
}

/// Keeps errors and slow spans, samples the rest at a rate that follows demand, and caps output
/// bytes per window.
#[napi]
pub struct AdaptiveSampling {
    inner: Mutex<core::AdaptiveSampling>,
}

#[napi]
impl AdaptiveSampling {
    /// `now` is the clock reading (seconds) the first window starts from.
    #[napi(constructor)]
    pub fn new(now: f64, config: Option<AdaptiveConfig>) -> Result<AdaptiveSampling> {
        let c = config.unwrap_or(AdaptiveConfig {
            base_rate: None,
            min_rate: None,
            max_rate: None,
            keep_at: None,
            slow_ms: None,
            max_bytes_per_second: None,
            window_seconds: None,
        });
        let options = core::AdaptiveOptions {
            base_rate: c.base_rate.unwrap_or(0.1),
            min_rate: c.min_rate.unwrap_or(0.01),
            max_rate: c.max_rate.unwrap_or(1.0),
            keep_at: level(c.keep_at.as_deref().unwrap_or("ERROR"))?,
            slow_ms: c.slow_ms.unwrap_or(1000.0),
            max_bytes_per_second: c
                .max_bytes_per_second
                .map_or(100_000, |n| n.max(0.0) as u64),
            window_seconds: c.window_seconds.unwrap_or(1.0),
        };
        Ok(AdaptiveSampling {
            inner: Mutex::new(core::AdaptiveSampling::new(options, now).map_err(reason)?),
        })
    }

    /// Whether to keep `record`, at clock reading `now` and random draw `draw` in `[0, 1)`.
    #[napi]
    pub fn decide(&self, record: String, now: f64, draw: f64) -> Result<bool> {
        let value = parse(&record, "the record")?;
        Ok(locked(&self.inner).decide(&value, now, draw))
    }

    /// The sample rate in force now.
    #[napi(getter)]
    pub fn rate(&self) -> f64 {
        locked(&self.inner).rate()
    }

    /// Bytes this window would have written (`demand`) and did write (`emitted`).
    #[napi]
    pub fn window_bytes(&self) -> WindowBytes {
        let (demand, emitted) = locked(&self.inner).window_bytes();
        WindowBytes {
            demand: demand as f64,
            emitted: emitted as f64,
        }
    }
}

/// What the current window of `AdaptiveSampling` has seen and let through.
#[napi(object)]
pub struct WindowBytes {
    pub demand: f64,
    pub emitted: f64,
}

/// Options for `FlightRecorder`; every field is optional.
#[napi(object)]
pub struct FlightConfig {
    pub run_key: Option<String>,
    pub ship_at: Option<String>,
    pub flush_at: Option<String>,
    pub max_buffered_records: Option<u32>,
    pub max_runs: Option<u32>,
    pub max_flushed: Option<u32>,
}

/// Holds a run's low-level records and ships them only if the run errors.
#[napi]
pub struct FlightRecorder {
    inner: Mutex<core::FlightRecorder>,
}

#[napi]
impl FlightRecorder {
    #[napi(constructor)]
    pub fn new(config: Option<FlightConfig>) -> Result<FlightRecorder> {
        let c = config.unwrap_or(FlightConfig {
            run_key: None,
            ship_at: None,
            flush_at: None,
            max_buffered_records: None,
            max_runs: None,
            max_flushed: None,
        });
        let options = core::FlightOptions {
            run_key: c.run_key.unwrap_or_else(|| "run_id".to_string()),
            ship_at: level(c.ship_at.as_deref().unwrap_or("INFO"))?,
            flush_at: level(c.flush_at.as_deref().unwrap_or("ERROR"))?,
            max_buffered_records: c.max_buffered_records.map_or(1000, |n| n as usize),
            max_runs: c.max_runs.map_or(200, |n| n as usize),
            max_flushed: c.max_flushed.map_or(10_000, |n| n as usize),
        };
        Ok(FlightRecorder {
            inner: Mutex::new(core::FlightRecorder::new(options)),
        })
    }

    /// `{ kept, released }` for one record.
    #[napi]
    pub fn decide(&self, record: String) -> Result<Decision> {
        let value = parse(&record, "the record")?;
        Ok(decision(locked(&self.inner).decide(&value)))
    }

    /// How many records are buffered waiting on their run.
    #[napi]
    pub fn buffered_records(&self) -> u32 {
        u32::try_from(locked(&self.inner).buffered_records()).unwrap_or(u32::MAX)
    }

    /// The runs holding buffered records, as JSON text of an array.
    #[napi]
    pub fn buffered_runs(&self) -> String {
        Value::Array(locked(&self.inner).buffered_runs()).to_string()
    }

    /// The runs already shipped because they errored, as JSON text of an array.
    #[napi]
    pub fn flushed(&self) -> String {
        Value::Array(locked(&self.inner).flushed()).to_string()
    }
}

/// Options for `RunSummary`; every field is optional.
#[napi(object)]
pub struct SummaryConfig {
    pub run_key: Option<String>,
    pub logger_name: Option<String>,
    pub max_runs: Option<u32>,
}

/// Totals a run's tokens, cost, tool calls, retries and errors, and emits one record when its root
/// span closes.
#[napi]
pub struct RunSummary {
    inner: Mutex<core::RunSummary>,
}

#[napi]
impl RunSummary {
    #[napi(constructor)]
    pub fn new(config: Option<SummaryConfig>) -> RunSummary {
        let c = config.unwrap_or(SummaryConfig {
            run_key: None,
            logger_name: None,
            max_runs: None,
        });
        RunSummary {
            inner: Mutex::new(core::RunSummary::new(core::SummaryOptions {
                run_key: c.run_key.unwrap_or_else(|| "run_id".to_string()),
                logger_name: c
                    .logger_name
                    .unwrap_or_else(|| "app.run_summary".to_string()),
                max_runs: c.max_runs.map_or(1000, |n| n as usize),
            })),
        }
    }

    /// Takes in one written record; returns the summary record (JSON text, stamped with
    /// `timestamp`) if this record closed its run's root span, otherwise `null`.
    #[napi]
    pub fn observe(&self, record: String, timestamp: String) -> Result<Option<String>> {
        let value = parse(&record, "the record")?;
        Ok(locked(&self.inner)
            .observe(&value, &timestamp)
            .map(|s| s.to_string()))
    }

    /// The runs whose root span has not closed yet, as JSON text of an array.
    #[napi]
    pub fn open_runs(&self) -> String {
        Value::Array(locked(&self.inner).open_runs()).to_string()
    }
}
