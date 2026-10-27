//! Logs-to-metrics: the plugin that turns records into counter and histogram events, and the
//! StatsD, Prometheus pushgateway and OTLP backends that receive them
//! (`docs/spec/02-plugins.md`, "MetricsPlugin").

use std::collections::BTreeMap;
use std::net::{SocketAddr, ToSocketAddrs, UdpSocket};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use logquill_core::{json_line, Level};
use serde_json::{json, Value};

use crate::http::HttpPost;

/// Where metric events go. Tags are `(key, value)` pairs.
pub trait MetricsBackend: Send {
    fn increment(&mut self, name: &str, tags: &[(String, String)]);
    fn observe(&mut self, name: &str, value: f64, tags: &[(String, String)]);
    /// Sends what is accumulated. Backends that push immediately do nothing.
    fn flush(&mut self) -> Result<(), String> {
        Ok(())
    }
    fn close(&mut self) -> Result<(), String> {
        self.flush()
    }
}

/// Counts errors by logger and records `meta.duration_ms` in a histogram.
pub struct MetricsPlugin {
    backend: Mutex<Box<dyn MetricsBackend>>,
    pub error_metric: String,
    pub duration_metric: String,
    pub error_at: Level,
}

impl MetricsPlugin {
    /// Defaults: `logquill.errors`, `logquill.duration_ms`, errors at ERROR.
    pub fn new(backend: Box<dyn MetricsBackend>) -> MetricsPlugin {
        MetricsPlugin {
            backend: Mutex::new(backend),
            error_metric: "logquill.errors".into(),
            duration_metric: "logquill.duration_ms".into(),
            error_at: Level::Error,
        }
    }

    /// Takes in one written record.
    pub fn observe(&self, record: &Value) {
        let logger = record
            .get("logger")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let tags = [("logger".to_string(), logger)];
        let mut backend = self.backend.lock().unwrap_or_else(|p| p.into_inner());
        let level = record
            .get("level")
            .and_then(Value::as_str)
            .and_then(Level::from_record_name);
        if level.is_some_and(|l| l >= self.error_at) {
            backend.increment(&self.error_metric, &tags);
        }
        if let Some(Value::Number(n)) = record.get("meta").and_then(|m| m.get("duration_ms")) {
            if let Ok(x) = n.to_string().parse::<f64>() {
                backend.observe(&self.duration_metric, x, &tags);
            }
        }
    }

    pub fn flush(&self) -> Result<(), String> {
        self.backend
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .flush()
    }

    pub fn close(&self) -> Result<(), String> {
        self.backend
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .close()
    }
}

/// UDP, fire and forget: `name:1|c` and `name:value|h`, with `|#key:value` tags.
pub struct StatsdBackend {
    socket: Option<UdpSocket>,
    target: String,
    /// The target's address, looked up once and kept until a send fails.
    resolved: Option<SocketAddr>,
    /// After a failed lookup, don't try again before this moment.
    retry_lookup_at: Option<Instant>,
    prefix: Option<String>,
}

/// How long a failed host lookup is remembered, so an unresolvable host costs one lookup per
/// interval and not one per metric.
const LOOKUP_RETRY: Duration = Duration::from_secs(30);

impl StatsdBackend {
    pub fn new(host: &str, port: u16, prefix: Option<&str>) -> StatsdBackend {
        StatsdBackend {
            socket: UdpSocket::bind("0.0.0.0:0").ok(),
            target: format!("{host}:{port}"),
            resolved: None,
            retry_lookup_at: None,
            prefix: prefix.map(str::to_string),
        }
    }

    /// The datagram for one metric.
    pub fn line(&self, name: &str, value: &str, kind: &str, tags: &[(String, String)]) -> String {
        let name = self
            .prefix
            .as_ref()
            .map_or_else(|| name.to_string(), |p| format!("{p}.{name}"));
        let tags = if tags.is_empty() {
            String::new()
        } else {
            format!(
                "|#{}",
                tags.iter()
                    .map(|(k, v)| format!("{k}:{v}"))
                    .collect::<Vec<_>>()
                    .join(",")
            )
        };
        format!("{name}:{value}|{kind}{tags}")
    }

    fn send(&mut self, line: &str) {
        // A failed send is dropped: metrics must never get in the way of logging.
        let Some(socket) = &self.socket else {
            return;
        };
        let target = match self.resolved {
            Some(target) => target,
            None => {
                if self.retry_lookup_at.is_some_and(|at| Instant::now() < at) {
                    return;
                }
                match self
                    .target
                    .to_socket_addrs()
                    .ok()
                    .and_then(|mut a| a.next())
                {
                    Some(target) => {
                        self.resolved = Some(target);
                        self.retry_lookup_at = None;
                        target
                    }
                    None => {
                        self.retry_lookup_at = Some(Instant::now() + LOOKUP_RETRY);
                        return;
                    }
                }
            }
        };
        if socket.send_to(line.as_bytes(), target).is_err() {
            self.resolved = None; // the address may have changed; look it up again
        }
    }
}

impl MetricsBackend for StatsdBackend {
    fn increment(&mut self, name: &str, tags: &[(String, String)]) {
        let line = self.line(name, "1", "c", tags);
        self.send(&line);
    }

    fn observe(&mut self, name: &str, value: f64, tags: &[(String, String)]) {
        let line = self.line(name, &logquill_core::python_float_repr(value), "h", tags);
        self.send(&line);
    }
}

fn sanitize(name: &str) -> String {
    let mut cleaned: String = name
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '_' || c == ':' {
                c
            } else {
                '_'
            }
        })
        .collect();
    if cleaned.chars().next().is_some_and(|c| c.is_ascii_digit()) {
        cleaned.insert(0, '_');
    }
    cleaned
}

type Series = (String, BTreeMap<String, String>);

/// How many distinct series (metric name plus tags) a backend keeps by default. A logger name
/// comes from the application and could be made per request, so the maps must not grow forever.
pub const DEFAULT_MAX_SERIES: usize = 1000;

/// Whether the series `key` can be recorded: it already exists, or there is room for a new one.
fn has_room<A, B>(
    own: &BTreeMap<String, A>,
    other: &BTreeMap<String, B>,
    key: &str,
    max: usize,
) -> bool {
    own.contains_key(key) || own.len() + other.len() < max
}

/// Accumulates counters and histogram sum / count in memory and PUTs them to a pushgateway.
pub struct PushgatewayBackend {
    url: String,
    job: String,
    timeout: Duration,
    poster: Box<dyn HttpPost>,
    counters: BTreeMap<String, (Series, f64)>,
    histograms: BTreeMap<String, (Series, f64, u64)>,
    /// The most distinct series kept; samples for further ones are dropped and counted.
    pub max_series: usize,
    dropped: u64,
}

impl PushgatewayBackend {
    pub fn new(
        gateway_url: &str,
        job_name: &str,
        timeout: Duration,
        poster: Box<dyn HttpPost>,
    ) -> PushgatewayBackend {
        PushgatewayBackend {
            url: gateway_url.trim_end_matches('/').to_string(),
            job: job_name.to_string(),
            timeout,
            poster,
            counters: BTreeMap::new(),
            histograms: BTreeMap::new(),
            max_series: DEFAULT_MAX_SERIES,
            dropped: 0,
        }
    }

    /// How many samples were dropped because `max_series` distinct series were already kept.
    pub fn dropped_samples(&self) -> u64 {
        self.dropped
    }

    fn series(name: &str, tags: &[(String, String)]) -> (String, Series) {
        let labels: BTreeMap<String, String> =
            tags.iter().map(|(k, v)| (sanitize(k), v.clone())).collect();
        let name = sanitize(name);
        (format!("{name}{labels:?}"), (name, labels))
    }

    fn labels(labels: &BTreeMap<String, String>) -> String {
        if labels.is_empty() {
            return String::new();
        }
        let pairs: Vec<String> = labels
            .iter()
            .map(|(k, v)| format!("{k}={}", json_line(&Value::String(v.clone()))))
            .collect();
        format!("{{{}}}", pairs.join(","))
    }

    /// The text body of a push, or `None` when nothing has been recorded.
    pub fn body(&self) -> Option<String> {
        let mut lines = Vec::new();
        for ((name, labels), value) in self.counters.values() {
            lines.push(format!(
                "{name}{} {}",
                Self::labels(labels),
                logquill_core::python_float_repr(*value)
            ));
        }
        for ((name, labels), sum, count) in self.histograms.values() {
            lines.push(format!(
                "{name}_sum{} {}",
                Self::labels(labels),
                logquill_core::python_float_repr(*sum)
            ));
            lines.push(format!("{name}_count{} {count}", Self::labels(labels)));
        }
        (!lines.is_empty()).then(|| lines.join("\n") + "\n")
    }
}

impl MetricsBackend for PushgatewayBackend {
    fn increment(&mut self, name: &str, tags: &[(String, String)]) {
        let (key, series) = Self::series(name, tags);
        if !has_room(&self.counters, &self.histograms, &key, self.max_series) {
            self.dropped += 1;
            return;
        }
        self.counters.entry(key).or_insert((series, 0.0)).1 += 1.0;
    }

    fn observe(&mut self, name: &str, value: f64, tags: &[(String, String)]) {
        let (key, series) = Self::series(name, tags);
        if !has_room(&self.histograms, &self.counters, &key, self.max_series) {
            self.dropped += 1;
            return;
        }
        let entry = self.histograms.entry(key).or_insert((series, 0.0, 0));
        entry.1 += value;
        entry.2 += 1;
    }

    fn flush(&mut self) -> Result<(), String> {
        let Some(body) = self.body() else {
            return Ok(());
        };
        let url = format!("{}/metrics/job/{}", self.url, self.job);
        let headers = [(
            "Content-Type".to_string(),
            "text/plain; version=0.0.4".to_string(),
        )];
        let response = self
            .poster
            .put(&url, &headers, body.as_bytes(), self.timeout)?;
        if response.status >= 400 {
            return Err(format!(
                "PrometheusPushgatewayBackend: pushgateway returned HTTP {} \u{2014} check the gateway URL is reachable and correct",
                response.status
            ));
        }
        Ok(())
    }
}

/// Accumulates counters and histograms and POSTs them as OTLP/HTTP JSON metrics.
pub struct OtlpMetricsBackend {
    endpoint: String,
    service_name: String,
    timeout: Duration,
    poster: Box<dyn HttpPost>,
    counters: BTreeMap<String, (Series, f64)>,
    histograms: BTreeMap<String, (Series, f64, u64)>,
    /// The most distinct series kept; samples for further ones are dropped and counted.
    pub max_series: usize,
    dropped: u64,
}

impl OtlpMetricsBackend {
    pub fn new(
        endpoint: &str,
        service_name: &str,
        timeout: Duration,
        poster: Box<dyn HttpPost>,
    ) -> OtlpMetricsBackend {
        OtlpMetricsBackend {
            endpoint: endpoint.to_string(),
            service_name: service_name.to_string(),
            timeout,
            poster,
            counters: BTreeMap::new(),
            histograms: BTreeMap::new(),
            max_series: DEFAULT_MAX_SERIES,
            dropped: 0,
        }
    }

    /// How many samples were dropped because `max_series` distinct series were already kept.
    pub fn dropped_samples(&self) -> u64 {
        self.dropped
    }

    fn attrs(labels: &BTreeMap<String, String>) -> Value {
        Value::Array(
            labels
                .iter()
                .map(|(k, v)| json!({"key": k, "value": {"stringValue": v}}))
                .collect(),
        )
    }

    /// The OTLP metrics document, or `None` when nothing has been recorded.
    pub fn document(&self, now_ns: u128) -> Option<Value> {
        let mut metrics = Vec::new();
        for ((name, labels), value) in self.counters.values() {
            metrics.push(json!({"name": name, "sum": {
                "aggregationTemporality": 2, "isMonotonic": true,
                "dataPoints": [{"attributes": Self::attrs(labels), "timeUnixNano": now_ns.to_string(), "asDouble": value}],
            }}));
        }
        for ((name, labels), sum, count) in self.histograms.values() {
            metrics.push(json!({"name": name, "histogram": {
                "aggregationTemporality": 2,
                "dataPoints": [{"attributes": Self::attrs(labels), "timeUnixNano": now_ns.to_string(), "count": count.to_string(), "sum": sum}],
            }}));
        }
        (!metrics.is_empty()).then(|| {
            json!({"resourceMetrics": [{
                "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": self.service_name}}]},
                "scopeMetrics": [{"scope": {"name": "logquill"}, "metrics": metrics}],
            }]})
        })
    }
}

impl MetricsBackend for OtlpMetricsBackend {
    fn increment(&mut self, name: &str, tags: &[(String, String)]) {
        let (key, _) = PushgatewayBackend::series(name, tags);
        if !has_room(&self.counters, &self.histograms, &key, self.max_series) {
            self.dropped += 1;
            return;
        }
        let labels = tags.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        self.counters
            .entry(key)
            .or_insert(((name.to_string(), labels), 0.0))
            .1 += 1.0;
    }

    fn observe(&mut self, name: &str, value: f64, tags: &[(String, String)]) {
        let (key, _) = PushgatewayBackend::series(name, tags);
        if !has_room(&self.histograms, &self.counters, &key, self.max_series) {
            self.dropped += 1;
            return;
        }
        let labels = tags.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        let entry = self
            .histograms
            .entry(key)
            .or_insert(((name.to_string(), labels), 0.0, 0));
        entry.1 += value;
        entry.2 += 1;
    }

    fn flush(&mut self) -> Result<(), String> {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos());
        let Some(document) = self.document(now) else {
            return Ok(());
        };
        let headers = [("Content-Type".to_string(), "application/json".to_string())];
        let response = self.poster.post(
            &self.endpoint,
            &headers,
            json_line(&document).as_bytes(),
            self.timeout,
        )?;
        if response.status >= 400 {
            return Err(format!(
                "OtlpMetricsBackend: collector returned HTTP {} \u{2014} check the metrics endpoint",
                response.status
            ));
        }
        Ok(())
    }
}
