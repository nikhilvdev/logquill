//! Metrics backends: StatsD over UDP and the Prometheus pushgateway, done in Rust. The Python
//! `MetricsPlugin` calls `increment` and `observe`; the pushgateway backend keeps its totals in
//! memory, bounded to a set number of series, and sends them on `flush`.

use std::sync::Mutex;
use std::time::Duration;

use logquill_runtime::{MetricsBackend, PushgatewayBackend, StatsdBackend, UreqPost};
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

fn locked<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|p| p.into_inner())
}

/// UDP, fire and forget: `name:1|c` and `name:value|h`, with `|#key:value` tags.
#[pyclass(name = "StatsdBackend", frozen)]
pub struct PyStatsd {
    inner: Mutex<StatsdBackend>,
}

#[pymethods]
impl PyStatsd {
    #[new]
    #[pyo3(signature = (host = "localhost", port = 8125, prefix = None))]
    fn new(host: &str, port: u16, prefix: Option<&str>) -> PyStatsd {
        PyStatsd {
            inner: Mutex::new(StatsdBackend::new(host, port, prefix)),
        }
    }

    fn increment(&self, name: &str, tags: Vec<(String, String)>) {
        locked(&self.inner).increment(name, &tags);
    }

    fn observe(&self, name: &str, value: f64, tags: Vec<(String, String)>) {
        locked(&self.inner).observe(name, value, &tags);
    }
}

/// Accumulates counters and histogram sum and count, and PUTs them to a pushgateway.
#[pyclass(name = "PushgatewayBackend", frozen)]
pub struct PyPushgateway {
    inner: Mutex<PushgatewayBackend>,
}

#[pymethods]
impl PyPushgateway {
    #[new]
    #[pyo3(signature = (gateway_url, job_name = "logquill", timeout = 5.0, max_series = 1000))]
    fn new(gateway_url: &str, job_name: &str, timeout: f64, max_series: usize) -> PyPushgateway {
        let mut backend = PushgatewayBackend::new(
            gateway_url,
            job_name,
            Duration::from_secs_f64(timeout),
            Box::new(UreqPost),
        );
        backend.max_series = max_series;
        PyPushgateway {
            inner: Mutex::new(backend),
        }
    }

    fn increment(&self, name: &str, tags: Vec<(String, String)>) {
        locked(&self.inner).increment(name, &tags);
    }

    fn observe(&self, name: &str, value: f64, tags: Vec<(String, String)>) {
        locked(&self.inner).observe(name, value, &tags);
    }

    /// Sends every series accumulated so far; raises if the gateway refuses.
    fn flush(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| locked(&self.inner).flush())
            .map_err(PyRuntimeError::new_err)
    }

    /// How many samples were dropped because `max_series` distinct series were already kept.
    fn dropped_samples(&self) -> u64 {
        locked(&self.inner).dropped_samples()
    }
}
