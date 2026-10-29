//! The plugins that keep state between records, as pipeline steps: probabilistic sampling, a per-key rate limit and adaptive
//! sampling. The core types take the clock reading and the random draw as arguments; here the logger supplies both.
//!
//! Not here: tail sampling and the flight recorder, which release *earlier* records when a later one errors. A pipeline step
//! can only keep or drop the record in front of it, so those need a way to write released records and are a separate piece
//! of work.

use logquill_core::{
    AdaptiveOptions, AdaptiveSampling, Clock, Plugin, RateKey, RateLimit, Sampling,
    SamplingOptions, SystemClock, Verdict,
};
use serde_json::{Map, Value};

use crate::logger::{only, parse_level, string_of};

/// A xorshift64* generator seeded from the clock: sampling needs spread, not unpredictability.
struct Draw(u64);

impl Draw {
    fn new() -> Draw {
        // `| 1` keeps the state non-zero, which xorshift cannot leave
        Draw((SystemClock.now_unix_micros() as u64 ^ 0x9E37_79B9_7F4A_7C15) | 1)
    }

    /// A uniform value in `[0, 1)`.
    fn next(&mut self) -> f64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        (self.0.wrapping_mul(0x2545_F491_4F6C_DD1D) >> 11) as f64 / (1u64 << 53) as f64
    }
}

fn seconds() -> f64 {
    SystemClock.now_unix_micros() as f64 / 1e6
}

fn number(spec: &Map<String, Value>, key: &str, what: &str) -> Result<Option<f64>, String> {
    match spec.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(v) => v
            .as_f64()
            .map(Some)
            .ok_or_else(|| format!("{what}: '{key}' must be a number")),
    }
}

fn level_of(
    spec: &Map<String, Value>,
    key: &str,
    what: &str,
) -> Result<Option<logquill_core::Level>, String> {
    string_of(spec, key, what)?.map(parse_level).transpose()
}

pub(crate) struct Sample {
    sampling: Sampling,
    draw: Draw,
}

impl Plugin for Sample {
    fn name(&self) -> &str {
        "sampling"
    }

    fn before_log(&mut self, record: &mut Value) -> Result<Verdict, String> {
        let draw = self.draw.next();
        Ok(if self.sampling.decide(record, draw).kept {
            Verdict::Keep
        } else {
            Verdict::Drop
        })
    }
}

pub(crate) struct Limit(RateLimit);

impl Plugin for Limit {
    fn name(&self) -> &str {
        "rate_limit"
    }

    fn before_log(&mut self, record: &mut Value) -> Result<Verdict, String> {
        Ok(if self.0.allow(record, seconds()) {
            Verdict::Keep
        } else {
            Verdict::Drop
        })
    }
}

pub(crate) struct Adaptive {
    sampling: AdaptiveSampling,
    draw: Draw,
}

impl Plugin for Adaptive {
    fn name(&self) -> &str {
        "adaptive_sampling"
    }

    fn before_log(&mut self, record: &mut Value) -> Result<Verdict, String> {
        let draw = self.draw.next();
        Ok(if self.sampling.decide(record, seconds(), draw) {
            Verdict::Keep
        } else {
            Verdict::Drop
        })
    }
}

/// `{"type": "sampling", "rate": 0.1}`: keeps about that fraction of records.
pub(crate) fn sampling(spec: &Map<String, Value>) -> Result<Sample, String> {
    const WHAT: &str = "the sampling plugin";
    only(spec, &["type", "rate"], WHAT)?;
    let rate = number(spec, "rate", WHAT)?
        .ok_or("the sampling plugin needs a \"rate\" between 0 and 1 (tail sampling is not available through the C ABI yet)")?;
    Ok(Sample {
        sampling: Sampling::new(SamplingOptions::new(rate)).map_err(|e| format!("{WHAT}: {e}"))?,
        draw: Draw::new(),
    })
}

/// `{"type": "rate_limit", "max_records": 100, "per_seconds": 1, "key": "logger_level"}`.
pub(crate) fn rate_limit(spec: &Map<String, Value>) -> Result<Limit, String> {
    const WHAT: &str = "the rate_limit plugin";
    only(
        spec,
        &["type", "max_records", "per_seconds", "max_keys", "key"],
        WHAT,
    )?;
    let max_records = number(spec, "max_records", WHAT)?
        .filter(|n| n.fract() == 0.0 && *n >= 0.0)
        .ok_or("the rate_limit plugin needs \"max_records\", a whole number of at least 1")?
        as u64;
    let per_seconds = number(spec, "per_seconds", WHAT)?
        .ok_or("the rate_limit plugin needs \"per_seconds\", the window length in seconds")?;
    let max_keys = number(spec, "max_keys", WHAT)?.map_or(1000, |n| n as usize);
    let key = match string_of(spec, "key", WHAT)? {
        None | Some("logger_level") => RateKey::LoggerLevel,
        Some("logger") => RateKey::Logger,
        Some("message") => RateKey::Message,
        Some(other) => {
            return Err(format!(
                "{WHAT}: key is 'logger_level', 'logger' or 'message', got '{other}'"
            ))
        }
    };
    RateLimit::new(max_records, per_seconds, max_keys, key)
        .map(Limit)
        .map_err(|e| format!("{WHAT}: {e}"))
}

/// `{"type": "adaptive_sampling", "base_rate": 0.1, "max_bytes_per_second": 100000}`: keeps errors and slow spans.
pub(crate) fn adaptive(spec: &Map<String, Value>) -> Result<Adaptive, String> {
    const WHAT: &str = "the adaptive_sampling plugin";
    only(
        spec,
        &[
            "type",
            "base_rate",
            "min_rate",
            "max_rate",
            "keep_at",
            "slow_ms",
            "max_bytes_per_second",
            "window_seconds",
        ],
        WHAT,
    )?;
    let mut options = AdaptiveOptions::default();
    if let Some(n) = number(spec, "base_rate", WHAT)? {
        options.base_rate = n;
    }
    if let Some(n) = number(spec, "min_rate", WHAT)? {
        options.min_rate = n;
    }
    if let Some(n) = number(spec, "max_rate", WHAT)? {
        options.max_rate = n;
    }
    if let Some(level) = level_of(spec, "keep_at", WHAT)? {
        options.keep_at = level;
    }
    if let Some(n) = number(spec, "slow_ms", WHAT)? {
        options.slow_ms = n;
    }
    if let Some(n) = number(spec, "max_bytes_per_second", WHAT)? {
        options.max_bytes_per_second = n as u64;
    }
    if let Some(n) = number(spec, "window_seconds", WHAT)? {
        options.window_seconds = n;
    }
    Ok(Adaptive {
        sampling: AdaptiveSampling::new(options, seconds()).map_err(|e| format!("{WHAT}: {e}"))?,
        draw: Draw::new(),
    })
}
