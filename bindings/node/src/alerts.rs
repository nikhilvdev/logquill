//! Alerting on the core runtime for Node: the Slack, PagerDuty and email senders, and the `Alerting`
//! service that de-duplicates repeated errors inside a window and sends from a worker thread, so a slow
//! webhook or SMTP server never holds up the event loop.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use logquill_core::{AlertOptions, Level};
use logquill_runtime::{
    AlertSender as RuntimeSender, Alerting as RuntimeAlerting, EmailOptions, EmailSender,
    PagerDutySender, SlackSender, UreqPost,
};
use napi::bindgen_prelude::{AsyncTask, Task};
use napi::{Env, Error, Result};
use napi_derive::napi;
use serde_json::Value;

type Shared = Arc<Mutex<Box<dyn RuntimeSender>>>;

fn locked(shared: &Shared) -> std::sync::MutexGuard<'_, Box<dyn RuntimeSender>> {
    shared.lock().unwrap_or_else(|p| p.into_inner())
}

fn reason(message: impl Into<String>) -> Error {
    Error::from_reason(message.into())
}

/// Where an alert goes. Make one with `slackSender`, `pagerdutySender` or `emailSender`.
#[napi]
pub struct AlertSender {
    inner: Shared,
}

impl AlertSender {
    fn new(sender: impl RuntimeSender + 'static) -> AlertSender {
        AlertSender {
            inner: Arc::new(Mutex::new(Box::new(sender))),
        }
    }
}

#[napi]
impl AlertSender {
    /// Sends one alert now, on the calling thread: `record` (JSON text) and how many repeats it stands for.
    #[napi]
    pub fn send(&self, record: String, occurrences: f64) -> Result<()> {
        let value: Value = serde_json::from_str(&record)
            .map_err(|e| reason(format!("the record is not valid JSON ({e})")))?;
        locked(&self.inner)
            .send(&value, occurrences.max(1.0) as u64)
            .map_err(reason)
    }
}

/// A sender that shares one `AlertSender` with the `Alerting` service built from it.
struct SharedSender(Shared);

impl RuntimeSender for SharedSender {
    fn send(&mut self, record: &Value, occurrences: u64) -> std::result::Result<(), String> {
        locked(&self.0).send(record, occurrences)
    }
}

/// Posts to a Slack incoming webhook.
#[napi]
pub fn slack_sender(webhook_url: String, timeout: Option<f64>) -> AlertSender {
    AlertSender::new(SlackSender {
        webhook_url,
        timeout: Duration::from_secs_f64(timeout.unwrap_or(5.0).max(0.0)),
        poster: Box::new(UreqPost),
    })
}

/// Triggers a PagerDuty event (`url` overrides the Events API endpoint, for a proxy or a test).
#[napi]
pub fn pagerduty_sender(
    routing_key: String,
    timeout: Option<f64>,
    url: Option<String>,
) -> AlertSender {
    let mut sender = PagerDutySender::new(
        &routing_key,
        Duration::from_secs_f64(timeout.unwrap_or(5.0).max(0.0)),
        Box::new(UreqPost),
    );
    if let Some(url) = url {
        sender.url = url;
    }
    AlertSender::new(sender)
}

/// Options for `emailSender`.
#[napi(object)]
pub struct EmailConfig {
    pub smtp_host: String,
    pub smtp_port: u32,
    pub from_addr: String,
    pub to_addrs: Vec<String>,
    pub username: Option<String>,
    pub password: Option<String>,
    /// Upgrade the connection with STARTTLS (default true).
    pub use_tls: Option<bool>,
    /// Seconds to wait for the server (default 10).
    pub timeout: Option<f64>,
}

/// Sends an email through an SMTP server.
#[napi]
pub fn email_sender(config: EmailConfig) -> AlertSender {
    AlertSender::new(EmailSender::new(EmailOptions {
        smtp_host: config.smtp_host,
        smtp_port: config.smtp_port as u16,
        from_addr: config.from_addr,
        to_addrs: config.to_addrs,
        username: config.username,
        password: config.password,
        use_tls: config.use_tls.unwrap_or(true),
        timeout: Duration::from_secs_f64(config.timeout.unwrap_or(10.0).max(0.0)),
    }))
}

/// Options for `Alerting`; every field is optional.
#[napi(object)]
pub struct AlertingConfig {
    /// A record at or above this level raises an alert (default `ERROR`).
    pub threshold: Option<String>,
    /// How long a window collapses repeats, in seconds (default 300).
    pub window_seconds: Option<f64>,
    /// Distinct errors tracked at once; past this a new one still alerts, untracked (default 500).
    pub max_tracked_keys: Option<u32>,
    /// Collapse by message alone instead of level, logger and message.
    pub by_message: Option<bool>,
}

/// De-duplicates repeated errors inside a window and sends each alert from a worker thread.
#[napi]
pub struct Alerting {
    inner: Arc<RuntimeAlerting>,
}

#[napi]
impl Alerting {
    #[napi(constructor)]
    pub fn new(sender: &AlertSender, config: Option<AlertingConfig>) -> Result<Alerting> {
        let defaults = AlertOptions::default();
        let c = config.unwrap_or(AlertingConfig {
            threshold: None,
            window_seconds: None,
            max_tracked_keys: None,
            by_message: None,
        });
        let threshold = match c.threshold.as_deref() {
            None => defaults.threshold,
            Some(name) => Level::from_record_name(&name.to_ascii_uppercase())
                .ok_or_else(|| reason(format!("Unknown log level: {name}")))?,
        };
        let options = AlertOptions {
            threshold,
            window_seconds: c.window_seconds.unwrap_or(defaults.window_seconds),
            max_tracked_keys: c
                .max_tracked_keys
                .map_or(defaults.max_tracked_keys, |n| n as usize),
            by_message: c.by_message.unwrap_or(false),
        };
        let warn: Arc<dyn Fn(&str) + Send + Sync> =
            Arc::new(|message| eprintln!("logquill: {message}"));
        let sender: Box<dyn RuntimeSender> = Box::new(SharedSender(Arc::clone(&sender.inner)));
        RuntimeAlerting::new(options, sender, warn)
            .map(|inner| Alerting {
                inner: Arc::new(inner),
            })
            .map_err(reason)
    }

    /// Takes in one written record (JSON text); alerts if it is at the threshold and not a repeat.
    #[napi]
    pub fn observe(&self, record: String) -> Result<()> {
        let value: Value = serde_json::from_str(&record)
            .map_err(|e| reason(format!("the record is not valid JSON ({e})")))?;
        self.inner.observe(&value);
        Ok(())
    }

    /// Waits for queued alerts to be sent; blocks the calling thread (use `flush` to keep the event loop free).
    #[napi]
    pub fn flush_sync(&self, timeout_ms: Option<f64>) -> bool {
        self.inner
            .flush(timeout_ms.map(|ms| Duration::from_secs_f64(ms.max(0.0) / 1000.0)))
    }

    /// Like `flushSync`, off the main thread.
    #[napi(ts_return_type = "Promise<boolean>")]
    pub fn flush(&self, timeout_ms: Option<f64>) -> AsyncTask<AlertDrain> {
        AsyncTask::new(AlertDrain {
            inner: Arc::clone(&self.inner),
            timeout: timeout_ms.map(|ms| Duration::from_secs_f64(ms.max(0.0) / 1000.0)),
        })
    }

    /// Sends what is pending and stops.
    #[napi]
    pub fn close(&self) {
        self.inner.close();
    }
}

/// Waits for the alert queue on a worker-pool thread.
pub struct AlertDrain {
    inner: Arc<RuntimeAlerting>,
    timeout: Option<Duration>,
}

impl Task for AlertDrain {
    type Output = bool;
    type JsValue = bool;

    fn compute(&mut self) -> Result<bool> {
        Ok(self.inner.flush(self.timeout))
    }

    fn resolve(&mut self, _env: Env, output: bool) -> Result<bool> {
        Ok(output)
    }
}
