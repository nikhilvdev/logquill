//! Alerting: the core's dedupe state machine running on the runtime's timer and bounded send pool,
//! so noticing an error never waits on a webhook or an SMTP server. Senders are native (Slack,
//! PagerDuty, email) or a Python object with `send_alert(record, occurrences)`.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use logquill_core::{AlertOptions, Level};
use logquill_runtime::{
    AlertSender, Alerting, EmailOptions, EmailSender, PagerDutySender, SlackSender, UreqPost,
};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use serde_json::Value;

use crate::convert::{to_py, to_value, NonFinite};

type SharedSender = Arc<Mutex<Box<dyn AlertSender>>>;

fn locked(sender: &SharedSender) -> std::sync::MutexGuard<'_, Box<dyn AlertSender>> {
    sender.lock().unwrap_or_else(|p| p.into_inner())
}

/// A sender implemented in Rust. It can be called directly (`send`) and handed to an `Alerting`.
#[pyclass(name = "AlertSender", frozen)]
pub struct PyAlertSender {
    inner: SharedSender,
}

impl PyAlertSender {
    fn new(sender: impl AlertSender + 'static) -> PyAlertSender {
        PyAlertSender {
            inner: Arc::new(Mutex::new(Box::new(sender))),
        }
    }
}

#[pymethods]
impl PyAlertSender {
    /// Sends one alert now, on this thread. Raises if the destination refuses it.
    fn send(&self, py: Python<'_>, record: &Bound<'_, PyAny>, occurrences: u64) -> PyResult<()> {
        let value = to_value(record, NonFinite::Null)?;
        let inner = Arc::clone(&self.inner);
        py.detach(move || locked(&inner).send(&value, occurrences))
            .map_err(PyRuntimeError::new_err)
    }
}

struct SharedAlertSender(SharedSender);

impl AlertSender for SharedAlertSender {
    fn send(&mut self, record: &Value, occurrences: u64) -> Result<(), String> {
        locked(&self.0).send(record, occurrences)
    }
}

/// A Python object with `send_alert(record, occurrences)`.
struct HostAlertSender(Py<PyAny>);

impl AlertSender for HostAlertSender {
    fn send(&mut self, record: &Value, occurrences: u64) -> Result<(), String> {
        Python::attach(|py| {
            let record = to_py(py, record)?;
            self.0
                .bind(py)
                .call_method1("send_alert", (record, occurrences))
                .map(|_| ())
        })
        .map_err(|e| Python::attach(|py| format!("{}: {}", e.get_type(py), e.value(py))))
    }
}

/// Posts alerts to a Slack incoming webhook.
#[pyfunction]
#[pyo3(signature = (webhook_url, timeout = 5.0))]
pub fn slack_sender(webhook_url: &str, timeout: f64) -> PyAlertSender {
    PyAlertSender::new(SlackSender {
        webhook_url: webhook_url.to_string(),
        timeout: Duration::from_secs_f64(timeout),
        poster: Box::new(UreqPost),
    })
}

/// Triggers PagerDuty incidents through the Events API v2 (or the `url` given, for a test).
#[pyfunction]
#[pyo3(signature = (routing_key, timeout = 5.0, url = None))]
pub fn pagerduty_sender(routing_key: &str, timeout: f64, url: Option<&str>) -> PyAlertSender {
    let mut sender = PagerDutySender::new(
        routing_key,
        Duration::from_secs_f64(timeout),
        Box::new(UreqPost),
    );
    if let Some(url) = url {
        sender.url = url.to_string();
    }
    PyAlertSender::new(sender)
}

/// Sends one plain-text email per alert over SMTP.
#[pyfunction]
#[pyo3(signature = (smtp_host, smtp_port, from_addr, to_addrs, username = None, password = None, use_tls = true, timeout = 10.0))]
#[allow(clippy::too_many_arguments)]
pub fn email_sender(
    smtp_host: &str,
    smtp_port: u16,
    from_addr: &str,
    to_addrs: Vec<String>,
    username: Option<String>,
    password: Option<String>,
    use_tls: bool,
    timeout: f64,
) -> PyAlertSender {
    PyAlertSender::new(EmailSender::new(EmailOptions {
        smtp_host: smtp_host.to_string(),
        smtp_port,
        from_addr: from_addr.to_string(),
        to_addrs,
        username,
        password,
        use_tls,
        timeout: Duration::from_secs_f64(timeout),
    }))
}

/// Deduplicates repeated errors and sends alerts on a bounded pool.
#[pyclass(name = "Alerting", frozen)]
pub struct PyAlerting {
    inner: Arc<Alerting>,
}

#[pymethods]
impl PyAlerting {
    #[new]
    #[pyo3(signature = (sender, threshold = "ERROR", window_seconds = 300.0, max_tracked_keys = 500, by_message = false, warn = None))]
    fn new(
        sender: &Bound<'_, PyAny>,
        threshold: &str,
        window_seconds: f64,
        max_tracked_keys: usize,
        by_message: bool,
        warn: Option<Py<PyAny>>,
    ) -> PyResult<PyAlerting> {
        let level = Level::from_record_name(threshold)
            .ok_or_else(|| PyValueError::new_err(format!("Unknown log level: {threshold}")))?;
        let sender: Box<dyn AlertSender> = match sender.cast::<PyAlertSender>() {
            Ok(native) => Box::new(SharedAlertSender(Arc::clone(&native.get().inner))),
            Err(_) => Box::new(HostAlertSender(sender.clone().unbind())),
        };
        let warn: Arc<dyn Fn(&str) + Send + Sync> = Arc::new(move |message: &str| match &warn {
            Some(callable) => {
                let delivered = Python::attach(|py| callable.call1(py, (message,)).is_ok());
                if !delivered {
                    eprintln!("logquill: {message}");
                }
            }
            None => eprintln!("logquill: {message}"),
        });
        let options = AlertOptions {
            threshold: level,
            window_seconds,
            max_tracked_keys,
            by_message,
        };
        let alerting = Alerting::new(options, sender, warn).map_err(PyValueError::new_err)?;
        Ok(PyAlerting {
            inner: Arc::new(alerting),
        })
    }

    /// Takes in a record that was just written; sends an alert for it if it is the first of its
    /// kind in the window. Never waits on the destination.
    fn observe(&self, record: &Bound<'_, PyAny>) -> PyResult<()> {
        self.inner.observe(&to_value(record, NonFinite::Null)?);
        Ok(())
    }

    /// Waits for alerts already queued to be sent.
    #[pyo3(signature = (timeout = None))]
    fn flush(&self, py: Python<'_>, timeout: Option<f64>) -> bool {
        let inner = Arc::clone(&self.inner);
        py.detach(move || inner.flush(timeout.map(|s| Duration::from_secs_f64(s.max(0.0)))))
    }

    /// Cancels pending summaries, sends what is queued, and stops the threads.
    fn close(&self, py: Python<'_>) {
        let inner = Arc::clone(&self.inner);
        py.detach(move || inner.close());
    }
}
