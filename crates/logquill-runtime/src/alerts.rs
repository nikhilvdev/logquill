//! Alerting: the Slack, PagerDuty and email senders, and the service that runs the core's dedupe
//! state machine on a timer and sends on a bounded pool, so an alert can never block a log call
//! (`docs/spec/02-plugins.md`, "AlertingPlugin and senders").

use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use lettre::message::header::ContentType;
use lettre::transport::smtp::authentication::Credentials;
use lettre::{Message, SmtpTransport, Transport as _};
use logquill_core::{json_line, Alert, AlertDedupe, AlertOptions};
use serde_json::{json, Map, Value};

use crate::http::HttpPost;
use crate::worker::{Backpressure, Worker, WorkerOptions};

/// Delivers one alert. `occurrences` is 1 for the first sighting and the total for a summary.
pub trait AlertSender: Send {
    fn send(&mut self, record: &Value, occurrences: u64) -> Result<(), String>;
}

fn text(record: &Value, key: &str) -> String {
    record
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or("")
        .to_string()
}

fn times(occurrences: u64) -> String {
    if occurrences > 1 {
        format!(" (x{occurrences})")
    } else {
        String::new()
    }
}

/// `[LEVEL] logger: message (xN)`.
pub fn slack_text(record: &Value, occurrences: u64) -> String {
    format!(
        "[{}] {}: {}{}",
        text(record, "level"),
        text(record, "logger"),
        text(record, "message"),
        times(occurrences)
    )
}

/// Posts to a Slack incoming webhook.
pub struct SlackSender {
    pub webhook_url: String,
    pub timeout: Duration,
    pub poster: Box<dyn HttpPost>,
}

impl AlertSender for SlackSender {
    fn send(&mut self, record: &Value, occurrences: u64) -> Result<(), String> {
        let body = json_line(&json!({"text": slack_text(record, occurrences)}));
        let headers = [("Content-Type".to_string(), "application/json".to_string())];
        let response =
            self.poster
                .post(&self.webhook_url, &headers, body.as_bytes(), self.timeout)?;
        if response.status >= 400 {
            return Err(format!(
                "SlackAlertPlugin: webhook returned HTTP {} \u{2014} check the webhook URL is still valid in Slack's app config",
                response.status
            ));
        }
        Ok(())
    }
}

/// The PagerDuty Events API v2 endpoint.
pub const PAGERDUTY_URL: &str = "https://events.pagerduty.com/v2/enqueue";

/// The Events API v2 trigger for an alert.
pub fn pagerduty_event(routing_key: &str, record: &Value, occurrences: u64) -> Value {
    let mut details = Map::new();
    details.insert("occurrences".into(), json!(occurrences));
    if let Some(meta) = record.get("meta").and_then(Value::as_object) {
        for (key, value) in meta {
            details.insert(key.clone(), value.clone());
        }
    }
    let severity = if text(record, "level") == "FATAL" {
        "critical"
    } else {
        "error"
    };
    json!({
        "routing_key": routing_key,
        "event_action": "trigger",
        "payload": {
            "summary": format!("{}: {}{}", text(record, "logger"), text(record, "message"), times(occurrences)),
            "severity": severity,
            "source": text(record, "logger"),
            "timestamp": record.get("timestamp"),
            "custom_details": details,
        },
    })
}

/// Triggers a PagerDuty incident.
pub struct PagerDutySender {
    pub routing_key: String,
    pub url: String,
    pub timeout: Duration,
    pub poster: Box<dyn HttpPost>,
}

impl PagerDutySender {
    /// Sends to the real Events API.
    pub fn new(routing_key: &str, timeout: Duration, poster: Box<dyn HttpPost>) -> PagerDutySender {
        PagerDutySender {
            routing_key: routing_key.into(),
            url: PAGERDUTY_URL.into(),
            timeout,
            poster,
        }
    }
}

impl AlertSender for PagerDutySender {
    fn send(&mut self, record: &Value, occurrences: u64) -> Result<(), String> {
        let body = json_line(&pagerduty_event(&self.routing_key, record, occurrences));
        let headers = [("Content-Type".to_string(), "application/json".to_string())];
        let response = self
            .poster
            .post(&self.url, &headers, body.as_bytes(), self.timeout)?;
        if response.status >= 400 {
            return Err(format!(
                "PagerDutyAlertPlugin: Events API returned HTTP {} \u{2014} check the routing key is a valid Events API v2 integration key",
                response.status
            ));
        }
        Ok(())
    }
}

/// Settings for the email sender.
#[derive(Clone, Debug)]
pub struct EmailOptions {
    pub smtp_host: String,
    pub smtp_port: u16,
    pub from_addr: String,
    pub to_addrs: Vec<String>,
    pub username: Option<String>,
    pub password: Option<String>,
    /// STARTTLS (the default). Turn off only for a local relay; credentials are refused without it.
    pub use_tls: bool,
    pub timeout: Duration,
}

/// Sends one plain-text email per alert over a fresh SMTP connection.
pub struct EmailSender {
    options: EmailOptions,
}

impl EmailSender {
    pub fn new(options: EmailOptions) -> EmailSender {
        EmailSender { options }
    }

    /// `(subject, body)` for an alert.
    pub fn compose(record: &Value, occurrences: u64) -> (String, String) {
        let subject = format!(
            "[{}] {}{}",
            text(record, "level"),
            text(record, "logger"),
            times(occurrences)
        );
        let meta = record
            .get("meta")
            .map_or_else(|| "{}".to_string(), json_line);
        let body = format!(
            "{}\n\noccurrences: {occurrences}\ntimestamp: {}\nmeta: {meta}",
            text(record, "message"),
            text(record, "timestamp"),
        );
        (subject, body)
    }
}

impl AlertSender for EmailSender {
    fn send(&mut self, record: &Value, occurrences: u64) -> Result<(), String> {
        let o = &self.options;
        if !o.use_tls && o.username.is_some() && o.password.is_some() {
            return Err("EmailAlertPlugin: refusing to send the SMTP username and password without TLS \u{2014} set use_tls to true, or drop the credentials for a local relay that needs none".to_string());
        }
        let (subject, body) = EmailSender::compose(record, occurrences);
        let fail = |e: &dyn std::fmt::Display| {
            format!("EmailAlertPlugin: couldn't send the alert \u{2014} {e}")
        };
        let mut builder = Message::builder()
            .from(o.from_addr.parse().map_err(|e| fail(&e))?)
            .subject(subject)
            .header(ContentType::TEXT_PLAIN);
        for to in &o.to_addrs {
            builder = builder.to(to.parse().map_err(|e| fail(&e))?);
        }
        let message = builder.body(body).map_err(|e| fail(&e))?;
        let mut smtp = if o.use_tls {
            SmtpTransport::starttls_relay(&o.smtp_host).map_err(|e| fail(&e))?
        } else {
            SmtpTransport::builder_dangerous(&o.smtp_host)
        }
        .port(o.smtp_port)
        .timeout(Some(o.timeout));
        if let (Some(user), Some(pass)) = (&o.username, &o.password) {
            smtp = smtp.credentials(Credentials::new(user.clone(), pass.clone()));
        }
        smtp.build()
            .send(&message)
            .map(|_| ())
            .map_err(|e| fail(&e))
    }
}

struct Shared {
    dedupe: Mutex<AlertDedupe>,
    wake: Condvar,
    stopped: Mutex<bool>,
    started: Instant,
    sender: Mutex<Box<dyn AlertSender>>,
    warn: Arc<dyn Fn(&str) + Send + Sync>,
}

impl Shared {
    fn now(&self) -> f64 {
        self.started.elapsed().as_secs_f64()
    }
}

/// Runs the dedupe state machine and sends alerts on a small bounded pool. Observing a record
/// never waits on a network call; when the pool is full the alert is dropped, and logging goes on.
pub struct Alerting {
    shared: Arc<Shared>,
    pool: Arc<Worker>,
    ticker: Mutex<Option<JoinHandle<()>>>,
}

fn dispatch(shared: &Arc<Shared>, pool: &Worker, alerts: Vec<Alert>) {
    for alert in alerts {
        let shared = shared.clone();
        pool.submit(move || {
            let mut sender = shared.sender.lock().unwrap_or_else(|p| p.into_inner());
            if let Err(error) = sender.send(&alert.record, alert.occurrences) {
                (shared.warn)(&error);
            }
        });
    }
}

impl Alerting {
    /// Starts the timer thread and the send pool.
    pub fn new(
        options: AlertOptions,
        sender: Box<dyn AlertSender>,
        warn: Arc<dyn Fn(&str) + Send + Sync>,
    ) -> Result<Alerting, String> {
        let shared = Arc::new(Shared {
            dedupe: Mutex::new(AlertDedupe::new(options)),
            wake: Condvar::new(),
            stopped: Mutex::new(false),
            started: Instant::now(),
            sender: Mutex::new(sender),
            warn: warn.clone(),
        });
        let pool = Arc::new(Worker::new(WorkerOptions {
            max_queue_size: 100,
            backpressure: Backpressure::DropNewest,
            warn_interval: Duration::from_secs(60),
            warn,
        })?);
        let ticker = {
            let (shared, pool) = (shared.clone(), pool.clone());
            thread::Builder::new()
                .name("logquill-alerts".into())
                .spawn(move || tick(&shared, &pool))
                .map_err(|e| e.to_string())?
        };
        Ok(Alerting {
            shared,
            pool,
            ticker: Mutex::new(Some(ticker)),
        })
    }

    /// Takes in a record that was just written.
    pub fn observe(&self, record: &Value) {
        let alerts = {
            let mut dedupe = self.shared.dedupe.lock().unwrap_or_else(|p| p.into_inner());
            dedupe.observe(record, self.shared.now())
        };
        self.shared.wake.notify_all(); // a new window may end before the one the timer waits for
        dispatch(&self.shared, &self.pool, alerts);
    }

    /// Waits for alerts already queued to be sent.
    pub fn flush(&self, timeout: Option<Duration>) -> bool {
        self.pool.drain(timeout)
    }

    /// Cancels pending summaries, sends what is queued, and stops the threads.
    pub fn close(&self) {
        {
            // `stopped` is set while holding the dedupe lock the timer waits on, so the timer either
            // sees it before waiting or is woken by the notification below; it cannot miss both.
            let mut dedupe = self.shared.dedupe.lock().unwrap_or_else(|p| p.into_inner());
            dedupe.close();
            *self
                .shared
                .stopped
                .lock()
                .unwrap_or_else(|p| p.into_inner()) = true;
        }
        self.shared.wake.notify_all();
        if let Some(handle) = self.ticker.lock().unwrap_or_else(|p| p.into_inner()).take() {
            let _ = handle.join();
        }
        self.pool.close(Some(Duration::from_secs(5)));
    }
}

impl Drop for Alerting {
    fn drop(&mut self) {
        self.close();
    }
}

fn tick(shared: &Arc<Shared>, pool: &Worker) {
    // The lock is held from reading the clock to waiting, and `wait_timeout` releases it
    // atomically, so an `observe` or a `close` can never slip in between and go unnoticed.
    let mut dedupe = shared.dedupe.lock().unwrap_or_else(|p| p.into_inner());
    loop {
        if *shared.stopped.lock().unwrap_or_else(|p| p.into_inner()) {
            return;
        }
        let now = shared.now();
        let alerts = dedupe.advance(now);
        let wait = dedupe
            .next_due()
            .map_or(3600.0, |due| (due - now).max(0.001));
        // Submitting never blocks (the pool drops when full), so this is safe under the lock.
        dispatch(shared, pool, alerts);
        dedupe = shared
            .wake
            .wait_timeout(dedupe, Duration::from_secs_f64(wait.min(3600.0)))
            .unwrap_or_else(|p| p.into_inner())
            .0;
    }
}
