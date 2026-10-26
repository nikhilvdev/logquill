//! The stateful plugins as pure state machines: each takes the record, the clock reading and the
//! random draw it needs, and says what to do. The core has no clock, threads or random source.
//! Every table is bounded (`docs/spec/02-plugins.md`; decisions D24, D44, D45).

use serde_json::{json, Map, Value};

use crate::jsonx::{is_integer, json_line};
use crate::ordered::{value_key, Ordered};
use crate::Level;

fn weight(record: &Value) -> u8 {
    record
        .get("level")
        .and_then(Value::as_str)
        .and_then(Level::from_record_name)
        .map_or(0, Level::weight)
}

fn meta_of(record: &Value) -> Option<&Map<String, Value>> {
    record.get("meta").and_then(Value::as_object)
}

fn meta_value<'a>(record: &'a Value, key: &str) -> Option<&'a Value> {
    meta_of(record).and_then(|m| m.get(key))
}

fn number(value: Option<&Value>) -> Option<f64> {
    match value {
        Some(Value::Number(n)) => n.to_string().parse().ok(),
        _ => None,
    }
}

/// A number as a message shows it: whole numbers without a fraction (`-1`), others as JSON.
fn plain(x: f64) -> String {
    if x.fract() == 0.0 && x.abs() < 1e15 {
        format!("{}", x as i64)
    } else {
        crate::jsonx::float_value(x).to_string()
    }
}

/// What a plugin decided about one record.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Decision {
    /// Whether the record continues down the pipeline.
    pub kept: bool,
    /// Earlier records released by this one, to be written straight to the transports.
    pub released: Vec<Value>,
}

// ---------------------------------------------------------------------------------------------

/// A table of per-key record buffers bounded by key count and total record count.
#[derive(Clone, Debug, Default)]
struct Buffers {
    table: Ordered<(Value, Vec<Value>)>,
    count: usize,
}

impl Buffers {
    fn push(&mut self, key: &str, id: &Value, record: &Value, max_keys: usize, max_records: usize) {
        if self.table.contains(key) {
            self.table.touch(key);
        } else {
            if self.table.len() >= max_keys {
                self.evict();
            }
            self.table.insert(key.to_string(), (id.clone(), Vec::new()));
        }
        if let Some((_, records)) = self.table.get_mut(key) {
            records.push(record.clone());
        }
        self.count += 1;
        while self.count > max_records && self.table.len() > 0 {
            self.evict();
        }
    }

    fn evict(&mut self) {
        if let Some((_, records)) = self.table.pop_oldest() {
            self.count -= records.len();
        }
    }

    fn take(&mut self, key: &str) -> Vec<Value> {
        let records = self.table.remove(key).map(|(_, r)| r).unwrap_or_default();
        self.count -= records.len();
        records
    }

    fn ids(&self) -> Vec<Value> {
        self.table.values().map(|(id, _)| id.clone()).collect()
    }
}

/// Options for [`Sampling`].
#[derive(Clone, Debug)]
pub struct SamplingOptions {
    pub rate: f64,
    /// Keep a trace's dropped records and release them when any record of it reaches `elevate_at`.
    pub tail: bool,
    pub trace_key: String,
    pub elevate_at: Level,
    pub max_buffered_records: usize,
    pub max_traces: usize,
    pub max_elevated: usize,
}

impl SamplingOptions {
    /// Plain probabilistic sampling at `rate`.
    pub fn new(rate: f64) -> SamplingOptions {
        SamplingOptions {
            rate,
            tail: false,
            trace_key: "trace_id".into(),
            elevate_at: Level::Error,
            max_buffered_records: 1000,
            max_traces: 200,
            max_elevated: 10_000,
        }
    }
}

/// Keeps roughly `rate` of records; with `tail`, a sampled-out trace is buffered and released if
/// it later errors.
#[derive(Clone, Debug)]
pub struct Sampling {
    options: SamplingOptions,
    buffers: Buffers,
    elevated: Ordered<Value>,
}

impl Sampling {
    /// A sampler, or the reason the rate is refused.
    pub fn new(options: SamplingOptions) -> Result<Sampling, String> {
        if !(0.0..=1.0).contains(&options.rate) {
            return Err(format!(
                "rate must be between 0 and 1, got {}",
                plain(options.rate)
            ));
        }
        Ok(Sampling {
            options,
            buffers: Buffers::default(),
            elevated: Ordered::default(),
        })
    }

    /// Decides one record given a random draw in `[0, 1)`.
    pub fn decide(&mut self, record: &Value, draw: f64) -> Decision {
        let trace = if self.options.tail {
            meta_value(record, &self.options.trace_key).filter(|v| !v.is_null())
        } else {
            None
        };
        let key = trace.map(value_key);
        if let Some(key) = &key {
            if self.elevated.contains(key) {
                self.elevated.touch(key);
                return Decision {
                    kept: true,
                    released: Vec::new(),
                };
            }
        }
        let keep = draw < self.options.rate;
        if let (Some(key), Some(trace)) = (&key, trace) {
            if weight(record) >= self.options.elevate_at.weight() {
                self.elevated.insert(key.clone(), trace.clone());
                while self.elevated.len() > self.options.max_elevated {
                    self.elevated.pop_oldest();
                }
                return Decision {
                    kept: true,
                    released: self.buffers.take(key),
                };
            }
        }
        if keep {
            return Decision {
                kept: true,
                released: Vec::new(),
            };
        }
        if let (Some(key), Some(trace)) = (&key, trace) {
            self.buffers.push(
                key,
                trace,
                record,
                self.options.max_traces,
                self.options.max_buffered_records,
            );
        }
        Decision::default()
    }

    /// Buffered trace ids, oldest first.
    pub fn buffered_traces(&self) -> Vec<Value> {
        self.buffers.ids()
    }

    /// How many records are buffered.
    pub fn buffered_records(&self) -> usize {
        self.buffers.count
    }

    /// Elevated trace ids, oldest first.
    pub fn elevated(&self) -> Vec<Value> {
        self.elevated.values().cloned().collect()
    }
}

// ---------------------------------------------------------------------------------------------

/// What identifies the stream a rate limit counts.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RateKey {
    LoggerLevel,
    Logger,
    Message,
}

/// Drops records once a key exceeds `max_records` inside its own rolling window.
#[derive(Clone, Debug)]
pub struct RateLimit {
    max_records: u64,
    per_seconds: f64,
    max_keys: usize,
    key: RateKey,
    windows: Ordered<(Value, f64, u64)>,
}

impl RateLimit {
    /// A limiter, or the reason its settings are refused.
    pub fn new(
        max_records: u64,
        per_seconds: f64,
        max_keys: usize,
        key: RateKey,
    ) -> Result<RateLimit, String> {
        if max_records < 1 {
            return Err(format!("max_records must be at least 1, got {max_records}"));
        }
        if per_seconds <= 0.0 {
            return Err(format!(
                "per_seconds must be positive, got {}",
                plain(per_seconds)
            ));
        }
        Ok(RateLimit {
            max_records,
            per_seconds,
            max_keys,
            key,
            windows: Ordered::default(),
        })
    }

    /// Whether `record` may pass at clock reading `now`.
    pub fn allow(&mut self, record: &Value, now: f64) -> bool {
        let field = |name: &str| record.get(name).cloned().unwrap_or(Value::Null);
        let key = match self.key {
            RateKey::LoggerLevel => json!([field("logger"), field("level")]),
            RateKey::Logger => field("logger"),
            RateKey::Message => field("message"),
        };
        let id = value_key(&key);
        match self.windows.get(&id).cloned() {
            Some((_, start, count)) if now - start < self.per_seconds => {
                self.windows.touch(&id);
                if count >= self.max_records {
                    return false;
                }
                self.windows.insert(id, (key, start, count + 1));
                true
            }
            _ => {
                self.windows.insert(id, (key, now, 1));
                while self.windows.len() > self.max_keys {
                    self.windows.pop_oldest();
                }
                true
            }
        }
    }

    /// Each tracked window as `[key, start, count]`, oldest first.
    pub fn windows(&self) -> Vec<(Value, f64, u64)> {
        self.windows.values().cloned().collect()
    }
}

// ---------------------------------------------------------------------------------------------

/// Options for [`AdaptiveSampling`].
#[derive(Clone, Debug)]
pub struct AdaptiveOptions {
    pub base_rate: f64,
    pub min_rate: f64,
    pub max_rate: f64,
    pub keep_at: Level,
    pub slow_ms: f64,
    pub max_bytes_per_second: u64,
    pub window_seconds: f64,
}

impl Default for AdaptiveOptions {
    fn default() -> Self {
        AdaptiveOptions {
            base_rate: 0.1,
            min_rate: 0.01,
            max_rate: 1.0,
            keep_at: Level::Error,
            slow_ms: 1000.0,
            max_bytes_per_second: 100_000,
            window_seconds: 1.0,
        }
    }
}

/// Keeps errors and slow spans, samples the rest at a rate that follows demand, and caps output
/// bytes per window.
#[derive(Clone, Debug)]
pub struct AdaptiveSampling {
    options: AdaptiveOptions,
    rate: f64,
    window_start: f64,
    demand_bytes: u64,
    emitted_bytes: u64,
}

impl AdaptiveSampling {
    /// A sampler whose first window starts at clock reading `now`, or the reason it is refused.
    pub fn new(options: AdaptiveOptions, now: f64) -> Result<AdaptiveSampling, String> {
        for (name, value) in [
            ("base_rate", options.base_rate),
            ("min_rate", options.min_rate),
            ("max_rate", options.max_rate),
        ] {
            if !(0.0..=1.0).contains(&value) {
                return Err(format!(
                    "{name} must be between 0 and 1, got {}",
                    plain(value)
                ));
            }
        }
        if options.min_rate > options.max_rate {
            return Err(format!(
                "min_rate ({}) must be <= max_rate ({})",
                plain(options.min_rate),
                plain(options.max_rate)
            ));
        }
        Ok(AdaptiveSampling {
            rate: options.base_rate,
            options,
            window_start: now,
            demand_bytes: 0,
            emitted_bytes: 0,
        })
    }

    /// Decides one record at clock reading `now` given a random draw in `[0, 1)`.
    pub fn decide(&mut self, record: &Value, now: f64, draw: f64) -> bool {
        if now - self.window_start >= self.options.window_seconds {
            let budget = self.options.max_bytes_per_second;
            let demand = if budget > 0 {
                self.demand_bytes as f64 / budget as f64
            } else {
                0.0
            };
            if demand < 0.5 {
                self.rate = (self.rate * 1.2).min(self.options.max_rate);
            } else if demand > 0.9 {
                self.rate = (self.rate * 0.5).max(self.options.min_rate);
            }
            self.window_start = now;
            self.demand_bytes = 0;
            self.emitted_bytes = 0;
        }
        let slow = meta_value(record, "kind").and_then(Value::as_str) == Some("span")
            && number(meta_value(record, "duration_ms")).is_some_and(|d| d >= self.options.slow_ms);
        if weight(record) >= self.options.keep_at.weight() || slow {
            return true;
        }
        if draw >= self.rate {
            return false;
        }
        let size = json_line(record).len() as u64;
        self.demand_bytes += size;
        if self.emitted_bytes + size > self.options.max_bytes_per_second {
            return false;
        }
        self.emitted_bytes += size;
        true
    }

    /// The current sampling rate.
    pub fn rate(&self) -> f64 {
        self.rate
    }

    /// Bytes of demand and of output in the current window.
    pub fn window_bytes(&self) -> (u64, u64) {
        (self.demand_bytes, self.emitted_bytes)
    }
}

// ---------------------------------------------------------------------------------------------

/// Options for [`FlightRecorder`].
#[derive(Clone, Debug)]
pub struct FlightOptions {
    pub run_key: String,
    pub ship_at: Level,
    pub flush_at: Level,
    pub max_buffered_records: usize,
    pub max_runs: usize,
    pub max_flushed: usize,
}

impl Default for FlightOptions {
    fn default() -> Self {
        FlightOptions {
            run_key: "run_id".into(),
            ship_at: Level::Info,
            flush_at: Level::Error,
            max_buffered_records: 1000,
            max_runs: 200,
            max_flushed: 10_000,
        }
    }
}

/// Holds a run's low-level records and ships them only if the run errors.
#[derive(Clone, Debug)]
pub struct FlightRecorder {
    options: FlightOptions,
    buffers: Buffers,
    flushed: Ordered<Value>,
}

impl FlightRecorder {
    pub fn new(options: FlightOptions) -> FlightRecorder {
        FlightRecorder {
            options,
            buffers: Buffers::default(),
            flushed: Ordered::default(),
        }
    }

    pub fn decide(&mut self, record: &Value) -> Decision {
        let kept = Decision {
            kept: true,
            released: Vec::new(),
        };
        let Some(run) = meta_value(record, &self.options.run_key).filter(|v| !v.is_null()) else {
            return kept;
        };
        let key = value_key(run);
        if self.flushed.contains(&key) {
            self.flushed.touch(&key);
            return kept;
        }
        let level = weight(record);
        if level >= self.options.flush_at.weight() {
            self.flushed.insert(key.clone(), run.clone());
            while self.flushed.len() > self.options.max_flushed {
                self.flushed.pop_oldest();
            }
            return Decision {
                kept: true,
                released: self.buffers.take(&key),
            };
        }
        if level >= self.options.ship_at.weight() {
            return kept;
        }
        self.buffers.push(
            &key,
            run,
            record,
            self.options.max_runs,
            self.options.max_buffered_records,
        );
        Decision::default()
    }

    pub fn buffered_runs(&self) -> Vec<Value> {
        self.buffers.ids()
    }

    pub fn buffered_records(&self) -> usize {
        self.buffers.count
    }

    pub fn flushed(&self) -> Vec<Value> {
        self.flushed.values().cloned().collect()
    }
}

// ---------------------------------------------------------------------------------------------

#[derive(Clone, Debug, Default)]
struct RunStats {
    run: Value,
    tokens_in: i128,
    tokens_out: i128,
    cost: f64,
    tools: u64,
    retries: i128,
    errors: u64,
    records: u64,
}

/// Options for [`RunSummary`].
#[derive(Clone, Debug)]
pub struct SummaryOptions {
    pub run_key: String,
    pub logger_name: String,
    pub max_runs: usize,
}

impl Default for SummaryOptions {
    fn default() -> Self {
        SummaryOptions {
            run_key: "run_id".into(),
            logger_name: "app.run_summary".into(),
            max_runs: 1000,
        }
    }
}

/// Totals a run's tokens, cost, tool calls, retries and errors, and emits one record when its
/// root span closes.
#[derive(Clone, Debug)]
pub struct RunSummary {
    options: SummaryOptions,
    runs: Ordered<RunStats>,
}

impl RunSummary {
    pub fn new(options: SummaryOptions) -> RunSummary {
        RunSummary {
            options,
            runs: Ordered::default(),
        }
    }

    /// Takes in one record (after it was written). Returns the summary record, stamped with
    /// `timestamp`, if this record closed its run's root span.
    pub fn observe(&mut self, record: &Value, timestamp: &str) -> Option<Value> {
        let meta = meta_of(record)?;
        let run = meta.get(&self.options.run_key).filter(|v| !v.is_null())?;
        let key = value_key(run);
        if self.runs.contains(&key) {
            self.runs.touch(&key);
        } else {
            if self.runs.len() >= self.options.max_runs {
                self.runs.pop_oldest();
            }
            self.runs.insert(
                key.clone(),
                RunStats {
                    run: run.clone(),
                    ..RunStats::default()
                },
            );
        }
        let stats = self.runs.get_mut(&key)?;
        stats.records += 1;
        if weight(record) >= Level::Error.weight() {
            stats.errors += 1;
        }
        if let Some(retries) = meta.get("retry_count").filter(|v| is_integer(v)) {
            stats.retries += retries.to_string().parse::<i128>().unwrap_or(0);
        }
        if meta.get("kind").and_then(Value::as_str) == Some("action")
            && meta.get("tool").is_some_and(Value::is_string)
        {
            stats.tools += 1;
        }
        if let Some(llm) = record.get("llm").and_then(Value::as_object) {
            for (field, total) in [
                ("tokens_in", &mut stats.tokens_in),
                ("tokens_out", &mut stats.tokens_out),
            ] {
                if let Some(n) = llm.get(field).filter(|v| is_integer(v)) {
                    *total += n.to_string().parse::<i128>().unwrap_or(0);
                }
            }
            if let Some(cost) = number(llm.get("cost_usd")) {
                stats.cost += cost;
            }
        }
        let is_root = meta.get("kind").and_then(Value::as_str) == Some("span")
            && meta.get("span_id").is_some_and(Value::is_string)
            && !meta.contains_key("parent_span_id");
        if !is_root {
            return None;
        }
        let stats = self.runs.remove(&key)?;
        let mut summary = Map::new();
        summary.insert("kind".into(), "run_summary".into());
        summary.insert(self.options.run_key.clone(), stats.run.clone());
        summary.insert("tokens_in".into(), Value::from(stats.tokens_in as i64));
        summary.insert("tokens_out".into(), Value::from(stats.tokens_out as i64));
        summary.insert("cost_usd".into(), crate::jsonx::float_value(stats.cost));
        summary.insert("tool_calls".into(), Value::from(stats.tools));
        summary.insert("retries".into(), Value::from(stats.retries as i64));
        summary.insert("errors".into(), Value::from(stats.errors));
        summary.insert("record_count".into(), Value::from(stats.records));
        if let Some(duration) = number(meta.get("duration_ms")) {
            summary.insert("duration_ms".into(), crate::jsonx::float_value(duration));
        }
        summary.insert(
            "parent_span_id".into(),
            meta.get("span_id").cloned().unwrap_or(Value::Null),
        );
        Some(json!({
            "schema_version": crate::SCHEMA_VERSION,
            "timestamp": timestamp,
            "level": "INFO",
            "logger": self.options.logger_name,
            "message": "run summary",
            "meta": summary,
        }))
    }

    /// Runs still open, oldest first.
    pub fn open_runs(&self) -> Vec<Value> {
        self.runs.values().map(|s| s.run.clone()).collect()
    }
}

// ---------------------------------------------------------------------------------------------

/// Stamps every record with the run id and a step counter.
#[derive(Clone, Debug)]
pub struct RunStamp {
    run_id: String,
    step: u64,
}

impl RunStamp {
    /// Uses `run_id` unless it is missing or empty, in which case `fresh()` supplies one
    /// (32 lower-case hex digits).
    pub fn new(run_id: Option<&str>, fresh: impl FnOnce() -> String) -> RunStamp {
        match run_id {
            Some(id) if !id.is_empty() => RunStamp {
                run_id: id.to_string(),
                step: 0,
            },
            _ => RunStamp {
                run_id: fresh(),
                step: 0,
            },
        }
    }

    pub fn run_id(&self) -> &str {
        &self.run_id
    }

    /// Adds `run_id` (if absent) and the next `step` to `meta`.
    pub fn stamp(&mut self, meta: &mut Map<String, Value>) {
        meta.entry("run_id")
            .or_insert_with(|| Value::String(self.run_id.clone()));
        meta.insert("step".into(), Value::from(self.step));
        self.step += 1;
    }

    pub fn steps(&self) -> u64 {
        self.step
    }
}

// ---------------------------------------------------------------------------------------------

/// One alert to send: the first occurrence has `occurrences == 1`; a summary of repeats carries the count.
#[derive(Clone, Debug, PartialEq)]
pub struct Alert {
    pub message: String,
    pub occurrences: u64,
}

/// Options for [`AlertDedupe`].
#[derive(Clone, Debug)]
pub struct AlertOptions {
    pub threshold: Level,
    pub window_seconds: f64,
    pub max_tracked_keys: usize,
    /// Collapse by message alone instead of level, logger and message.
    pub by_message: bool,
}

impl Default for AlertOptions {
    fn default() -> Self {
        AlertOptions {
            threshold: Level::Error,
            window_seconds: 300.0,
            max_tracked_keys: 500,
            by_message: false,
        }
    }
}

struct Window {
    message: String,
    count: u64,
    due: f64,
}

/// Collapses repeated errors inside a window into one alert plus a count. An alert is never
/// dropped for lack of room: with the table full it is sent at once and its repeats are not collapsed.
pub struct AlertDedupe {
    options: AlertOptions,
    windows: Ordered<Window>,
}

impl AlertDedupe {
    pub fn new(options: AlertOptions) -> AlertDedupe {
        AlertDedupe {
            options,
            windows: Ordered::default(),
        }
    }

    /// Releases the summaries of every window that has ended by clock reading `now`.
    pub fn advance(&mut self, now: f64) -> Vec<Alert> {
        let mut due: Vec<(String, f64)> = self
            .windows
            .keys()
            .filter_map(|k| self.windows.get(k).map(|w| (k.clone(), w.due)))
            .filter(|(_, d)| *d <= now)
            .collect();
        due.sort_by(|a, b| a.1.total_cmp(&b.1));
        let mut alerts = Vec::new();
        for (key, _) in due {
            if let Some(window) = self.windows.remove(&key) {
                if window.count > 1 {
                    alerts.push(Alert {
                        message: window.message,
                        occurrences: window.count,
                    });
                }
            }
        }
        alerts
    }

    /// Takes in one written record at `now`: ended windows are released first, then the record
    /// either joins an open window or alerts at once.
    pub fn observe(&mut self, record: &Value, now: f64) -> Vec<Alert> {
        let mut alerts = self.advance(now);
        if weight(record) < self.options.threshold.weight() {
            return alerts;
        }
        let text = |name: &str| {
            record
                .get(name)
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string()
        };
        let message = text("message");
        let key = if self.options.by_message {
            message.clone()
        } else {
            format!("{}:{}:{}", text("level"), text("logger"), message)
        };
        if let Some(window) = self.windows.get_mut(&key) {
            window.count += 1;
            return alerts;
        }
        if self.windows.len() < self.options.max_tracked_keys {
            self.windows.insert(
                key,
                Window {
                    message: message.clone(),
                    count: 1,
                    due: now + self.options.window_seconds,
                },
            );
        }
        alerts.push(Alert {
            message,
            occurrences: 1,
        });
        alerts
    }

    /// Forgets every open window without sending its summary.
    pub fn close(&mut self) {
        self.windows.clear();
    }

    pub fn tracked(&self) -> usize {
        self.windows.len()
    }
}
