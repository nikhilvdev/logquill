//! The core's stateful plugin logic as Python classes: sampling, rate limiting, adaptive sampling,
//! the flight recorder and the run summary. Each holds its state behind a lock, so one instance
//! can serve a logger used from many threads.

use std::sync::Mutex;

use logquill_core as core;
use logquill_core::Level;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyList;
use serde_json::Value;

use crate::convert::{to_py, to_value, NonFinite};

fn level(name: &str) -> PyResult<Level> {
    Level::from_record_name(name)
        .ok_or_else(|| PyValueError::new_err(format!("Unknown log level: {name}")))
}

fn record(value: &Bound<'_, PyAny>) -> PyResult<Value> {
    to_value(value, NonFinite::Null)
}

fn locked<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|p| p.into_inner())
}

/// `(kept, released)`: whether the record continues, and earlier records it frees.
fn decision<'py>(
    py: Python<'py>,
    decision: core::Decision,
) -> PyResult<(bool, Bound<'py, PyList>)> {
    let released = decision
        .released
        .iter()
        .map(|r| to_py(py, r))
        .collect::<PyResult<Vec<_>>>()?;
    Ok((decision.kept, PyList::new(py, released)?))
}

/// Keeps roughly `rate` of records; with `tail`, a sampled-out trace is buffered and released if it
/// later errors.
#[pyclass(name = "Sampling", frozen)]
pub struct PySampling {
    inner: Mutex<core::Sampling>,
}

#[pymethods]
impl PySampling {
    #[new]
    #[pyo3(signature = (rate, tail = false, trace_key = "trace_id", elevate_at = "ERROR", max_buffered_records = 1000, max_traces = 200, max_elevated = 10_000))]
    fn new(
        rate: f64,
        tail: bool,
        trace_key: &str,
        elevate_at: &str,
        max_buffered_records: usize,
        max_traces: usize,
        max_elevated: usize,
    ) -> PyResult<PySampling> {
        let options = core::SamplingOptions {
            rate,
            tail,
            trace_key: trace_key.to_string(),
            elevate_at: level(elevate_at)?,
            max_buffered_records,
            max_traces,
            max_elevated,
        };
        Ok(PySampling {
            inner: Mutex::new(core::Sampling::new(options).map_err(PyValueError::new_err)?),
        })
    }

    /// Decides one record given a random draw in `[0, 1)`.
    fn decide<'py>(
        &self,
        py: Python<'py>,
        value: &Bound<'py, PyAny>,
        draw: f64,
    ) -> PyResult<(bool, Bound<'py, PyList>)> {
        let value = record(value)?;
        decision(py, locked(&self.inner).decide(&value, draw))
    }

    /// How many records are buffered waiting on their trace.
    fn buffered_records(&self) -> usize {
        locked(&self.inner).buffered_records()
    }
}

/// Drops records once a stream exceeds `max_records` inside its own rolling window.
#[pyclass(name = "RateLimit", frozen)]
pub struct PyRateLimit {
    inner: Mutex<core::RateLimit>,
}

#[pymethods]
impl PyRateLimit {
    /// `key` is `"logger_level"`, `"logger"` or `"message"`.
    #[new]
    #[pyo3(signature = (max_records, per_seconds, max_keys = 1000, key = "logger_level"))]
    fn new(
        max_records: u64,
        per_seconds: f64,
        max_keys: usize,
        key: &str,
    ) -> PyResult<PyRateLimit> {
        let key = match key {
            "logger_level" => core::RateKey::LoggerLevel,
            "logger" => core::RateKey::Logger,
            "message" => core::RateKey::Message,
            other => {
                return Err(PyValueError::new_err(format!(
                    "key must be 'logger_level', 'logger' or 'message', got '{other}'"
                )))
            }
        };
        Ok(PyRateLimit {
            inner: Mutex::new(
                core::RateLimit::new(max_records, per_seconds, max_keys, key)
                    .map_err(PyValueError::new_err)?,
            ),
        })
    }

    /// Whether `record` may pass at clock reading `now` (seconds).
    fn allow(&self, value: &Bound<'_, PyAny>, now: f64) -> PyResult<bool> {
        let value = record(value)?;
        Ok(locked(&self.inner).allow(&value, now))
    }
}

/// Keeps errors and slow spans, samples the rest at a rate that follows demand, and caps output
/// bytes per window.
#[pyclass(name = "AdaptiveSampling", frozen)]
pub struct PyAdaptiveSampling {
    inner: Mutex<core::AdaptiveSampling>,
}

#[pymethods]
impl PyAdaptiveSampling {
    #[new]
    #[pyo3(signature = (now, base_rate = 0.1, min_rate = 0.01, max_rate = 1.0, keep_at = "ERROR", slow_ms = 1000.0, max_bytes_per_second = 100_000, window_seconds = 1.0))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        now: f64,
        base_rate: f64,
        min_rate: f64,
        max_rate: f64,
        keep_at: &str,
        slow_ms: f64,
        max_bytes_per_second: u64,
        window_seconds: f64,
    ) -> PyResult<PyAdaptiveSampling> {
        let options = core::AdaptiveOptions {
            base_rate,
            min_rate,
            max_rate,
            keep_at: level(keep_at)?,
            slow_ms,
            max_bytes_per_second,
            window_seconds,
        };
        Ok(PyAdaptiveSampling {
            inner: Mutex::new(
                core::AdaptiveSampling::new(options, now).map_err(PyValueError::new_err)?,
            ),
        })
    }

    /// Whether to keep `record`, at clock reading `now` and random draw `draw` in `[0, 1)`.
    fn decide(&self, value: &Bound<'_, PyAny>, now: f64, draw: f64) -> PyResult<bool> {
        let value = record(value)?;
        Ok(locked(&self.inner).decide(&value, now, draw))
    }

    /// The sample rate in force now.
    #[getter]
    fn rate(&self) -> f64 {
        locked(&self.inner).rate()
    }
}

/// Holds a run's low-level records and ships them only if the run errors.
#[pyclass(name = "FlightRecorder", frozen)]
pub struct PyFlightRecorder {
    inner: Mutex<core::FlightRecorder>,
}

#[pymethods]
impl PyFlightRecorder {
    #[new]
    #[pyo3(signature = (run_key = "run_id", ship_at = "INFO", flush_at = "ERROR", max_buffered_records = 1000, max_runs = 200, max_flushed = 10_000))]
    fn new(
        run_key: &str,
        ship_at: &str,
        flush_at: &str,
        max_buffered_records: usize,
        max_runs: usize,
        max_flushed: usize,
    ) -> PyResult<PyFlightRecorder> {
        let options = core::FlightOptions {
            run_key: run_key.to_string(),
            ship_at: level(ship_at)?,
            flush_at: level(flush_at)?,
            max_buffered_records,
            max_runs,
            max_flushed,
        };
        Ok(PyFlightRecorder {
            inner: Mutex::new(core::FlightRecorder::new(options)),
        })
    }

    /// `(kept, released)` for one record.
    fn decide<'py>(
        &self,
        py: Python<'py>,
        value: &Bound<'py, PyAny>,
    ) -> PyResult<(bool, Bound<'py, PyList>)> {
        let value = record(value)?;
        decision(py, locked(&self.inner).decide(&value))
    }

    /// How many records are buffered waiting on their run.
    fn buffered_records(&self) -> usize {
        locked(&self.inner).buffered_records()
    }
}

/// Totals a run's tokens, cost, tool calls, retries and errors, and emits one record when its root
/// span closes.
#[pyclass(name = "RunSummary", frozen)]
pub struct PyRunSummary {
    inner: Mutex<core::RunSummary>,
}

#[pymethods]
impl PyRunSummary {
    #[new]
    #[pyo3(signature = (run_key = "run_id", logger_name = "app.run_summary", max_runs = 1000))]
    fn new(run_key: &str, logger_name: &str, max_runs: usize) -> PyRunSummary {
        PyRunSummary {
            inner: Mutex::new(core::RunSummary::new(core::SummaryOptions {
                run_key: run_key.to_string(),
                logger_name: logger_name.to_string(),
                max_runs,
            })),
        }
    }

    /// Takes in one written record; returns the summary record (stamped with `timestamp`) if this
    /// record closed its run's root span.
    fn observe<'py>(
        &self,
        py: Python<'py>,
        value: &Bound<'py, PyAny>,
        timestamp: &str,
    ) -> PyResult<Option<Bound<'py, PyAny>>> {
        let value = record(value)?;
        let summary = locked(&self.inner).observe(&value, timestamp);
        summary.map(|s| to_py(py, &s)).transpose()
    }
}
