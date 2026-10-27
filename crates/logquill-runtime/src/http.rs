//! The HTTP NDJSON transport and the four HTTP cloud sinks (`docs/spec/05-runtime-and-transports.md`).
//!
//! All of them batch through [`Batching`](crate::Batching), POST once per batch, and drop a batch
//! whose delivery fails (no retry). The client is a trait, so tests talk to a real local server
//! and embedders can supply their own.

use std::io::Write as _;
use std::sync::Arc;
use std::time::Duration;

use logquill_core::{json_line, to_ecs_document};
use serde_json::{json, Map, Value};

use crate::batching::{BatchSender, Batching, ToItem};
use crate::transport::Formatter;

/// What a POST came back with.
#[derive(Clone, Debug, Default)]
pub struct HttpResponse {
    pub status: u16,
    /// The `Retry-After` header, if any.
    pub retry_after: Option<String>,
}

/// Something that can POST a body.
pub trait HttpPost: Send {
    /// Sends `body` to `url`. A non-2xx status is a normal response, not an error; an `Err` means
    /// the request never completed (connection refused, timeout).
    fn post(
        &mut self,
        url: &str,
        headers: &[(String, String)],
        body: &[u8],
        timeout: Duration,
    ) -> Result<HttpResponse, String>;

    /// Sends `body` with PUT (the Prometheus pushgateway needs it). Clients that cannot say so.
    fn put(
        &mut self,
        _url: &str,
        _headers: &[(String, String)],
        _body: &[u8],
        _timeout: Duration,
    ) -> Result<HttpResponse, String> {
        Err("this HTTP client does not support PUT".to_string())
    }
}

/// The default client.
pub struct UreqPost;

impl UreqPost {
    fn send(
        method: &str,
        url: &str,
        headers: &[(String, String)],
        body: &[u8],
        timeout: Duration,
    ) -> Result<HttpResponse, String> {
        let agent: ureq::Agent = ureq::Agent::config_builder()
            .timeout_global(Some(timeout))
            .http_status_as_error(false)
            .build()
            .into();
        let mut request = match method {
            "PUT" => agent.put(url),
            _ => agent.post(url),
        };
        for (name, value) in headers {
            request = request.header(name.as_str(), value.as_str());
        }
        let response = request.send(body).map_err(|e| e.to_string())?;
        Ok(HttpResponse {
            status: response.status().as_u16(),
            retry_after: response
                .headers()
                .get("retry-after")
                .and_then(|v| v.to_str().ok())
                .map(str::to_string),
        })
    }
}

impl HttpPost for UreqPost {
    fn post(
        &mut self,
        url: &str,
        headers: &[(String, String)],
        body: &[u8],
        timeout: Duration,
    ) -> Result<HttpResponse, String> {
        UreqPost::send("POST", url, headers, body, timeout)
    }

    fn put(
        &mut self,
        url: &str,
        headers: &[(String, String)],
        body: &[u8],
        timeout: Duration,
    ) -> Result<HttpResponse, String> {
        UreqPost::send("PUT", url, headers, body, timeout)
    }
}

/// Settings every HTTP sink shares.
#[derive(Clone, Debug)]
pub struct HttpOptions {
    pub max_records: usize,
    pub max_bytes: usize,
    pub timeout: Duration,
    pub formatter: Formatter,
}

impl Default for HttpOptions {
    fn default() -> Self {
        HttpOptions {
            max_records: 100,
            max_bytes: 1_000_000,
            timeout: Duration::from_secs(10),
            formatter: Formatter::Json,
        }
    }
}

fn header(name: &str, value: &str) -> (String, String) {
    (name.to_string(), value.to_string())
}

fn line_item() -> ToItem<String> {
    Box::new(|line: &str, _| (line.to_string(), line.len()))
}

// ---- plain NDJSON -----------------------------------------------------------------------

struct Ndjson {
    poster: Box<dyn HttpPost>,
    url: String,
    timeout: Duration,
}

impl BatchSender<String> for Ndjson {
    fn send(&mut self, batch: &[String]) -> Result<(), String> {
        let fail = |hint: &str| {
            format!(
                "HTTPTransport: couldn't deliver {} log record(s) to {} \u{2014} {hint}; those records were dropped",
                batch.len(),
                self.url
            )
        };
        let body = batch.join("\n");
        let headers = [header("Content-Type", "application/x-ndjson")];
        match self
            .poster
            .post(&self.url, &headers, body.as_bytes(), self.timeout)
        {
            Ok(response) if (200..300).contains(&response.status) => Ok(()),
            Ok(response) => Err(fail(&format!(
                "the server answered HTTP {}; check the URL accepts POSTed NDJSON",
                response.status
            ))),
            Err(_) => Err(fail(
                "check the URL is reachable and accepts POSTed NDJSON, or raise timeout",
            )),
        }
    }
}

/// POSTs batches of formatted lines, newline-joined, as `application/x-ndjson`.
pub fn http_transport(
    url: &str,
    options: HttpOptions,
    poster: Box<dyn HttpPost>,
) -> Batching<String> {
    let sender = Ndjson {
        poster,
        url: url.to_string(),
        timeout: options.timeout,
    };
    Batching::new(
        options.formatter,
        line_item(),
        Box::new(sender),
        options.max_records,
        options.max_bytes,
    )
}

// ---- a generic "POST this body, check the status" sender ----------------------------------

type BodyFn = Box<dyn Fn(&[String]) -> Result<Vec<u8>, String> + Send>;

struct Checked {
    name: &'static str,
    poster: Box<dyn HttpPost>,
    url: String,
    headers: Vec<(String, String)>,
    body: BodyFn,
    hint: &'static str,
    timeout: Duration,
}

impl BatchSender<String> for Checked {
    fn send(&mut self, batch: &[String]) -> Result<(), String> {
        let body = (self.body)(batch)?;
        match self
            .poster
            .post(&self.url, &self.headers, &body, self.timeout)
        {
            Ok(r) if (200..300).contains(&r.status) => Ok(()),
            Ok(r) => Err(format!(
                "{}: request to {} failed with status {} \u{2014} {}",
                self.name, self.url, r.status, self.hint
            )),
            Err(error) => Err(format!(
                "{}: request to {} failed \u{2014} {error}",
                self.name, self.url
            )),
        }
    }
}

/// Datadog log intake for `site` (default `datadoghq.com`). Records are always sent as JSON
/// objects in a JSON array, so the formatter is ignored, as for Elasticsearch and New Relic.
pub fn datadog_transport(
    api_key: &str,
    site: &str,
    options: HttpOptions,
    poster: Box<dyn HttpPost>,
) -> Batching<String> {
    let sender = Checked {
        name: "DatadogTransport",
        poster,
        url: format!("https://http-intake.logs.{site}/api/v2/logs"),
        headers: vec![
            header("Content-Type", "application/json"),
            header("DD-API-KEY", api_key),
        ],
        body: Box::new(|batch| Ok(format!("[{}]", batch.join(",")).into_bytes())),
        hint: "check the API key and site region",
        timeout: options.timeout,
    };
    let to_item: ToItem<String> = Box::new(|_, record| {
        let line = json_line(record);
        let size = line.len();
        (line, size)
    });
    Batching::new(
        options.formatter,
        to_item,
        Box::new(sender),
        options.max_records,
        options.max_bytes,
    )
}

/// The Datadog intake URL for a site, for callers that want to see it.
pub fn datadog_url(site: &str) -> String {
    format!("https://http-intake.logs.{site}/api/v2/logs")
}

/// Elasticsearch `_bulk` with Elastic Common Schema documents; the formatter is ignored.
pub fn elasticsearch_transport(
    url: &str,
    index: &str,
    options: HttpOptions,
    poster: Box<dyn HttpPost>,
) -> Batching<String> {
    let base = url.trim_end_matches('/').to_string();
    let action = json_line(&json!({"index": {"_index": index}}));
    let sender = Checked {
        name: "ElasticsearchTransport",
        poster,
        url: format!("{base}/_bulk"),
        headers: vec![header("Content-Type", "application/x-ndjson")],
        body: Box::new(move |batch| {
            let mut body = String::new();
            for document in batch {
                body.push_str(&action);
                body.push('\n');
                body.push_str(document);
                body.push('\n');
            }
            Ok(body.into_bytes())
        }),
        hint: "check the URL and index",
        timeout: options.timeout,
    };
    let to_item: ToItem<String> = Box::new(|_, record| {
        let line = json_line(&to_ecs_document(record));
        let size = line.len();
        (line, size)
    });
    Batching::new(
        options.formatter,
        to_item,
        Box::new(sender),
        options.max_records,
        options.max_bytes,
    )
}

fn severity(level: &str) -> u8 {
    match level {
        "INFO" => 1,
        "WARN" => 2,
        "ERROR" => 3,
        "FATAL" => 4,
        _ => 0,
    }
}

/// Azure Application Insights, posting newline-delimited trace envelopes.
pub fn app_insights_transport(
    instrumentation_key: &str,
    options: HttpOptions,
    poster: Box<dyn HttpPost>,
) -> Batching<String> {
    let url = "https://dc.services.visualstudio.com/v2/track".to_string();
    let sender = Checked {
        name: "AppInsightsTransport",
        poster,
        url,
        headers: vec![header("Content-Type", "application/x-json-stream")],
        body: Box::new(|batch| Ok(batch.join("\n").into_bytes())),
        hint: "check the instrumentation key",
        timeout: options.timeout,
    };
    let key = instrumentation_key.to_string();
    let to_item: ToItem<String> = Box::new(move |_, record| {
        let mut properties = Map::new();
        if let Some(meta) = record.get("meta").and_then(Value::as_object) {
            for (name, value) in meta {
                let text = match value {
                    Value::String(s) => s.clone(),
                    other => json_line(other),
                };
                properties.insert(name.clone(), Value::String(text));
            }
        }
        let envelope = json!({
            "name": "Microsoft.ApplicationInsights.Trace",
            "time": record.get("timestamp"),
            "iKey": key,
            "data": {"baseType": "MessageData", "baseData": {
                "ver": 2,
                "message": record.get("message"),
                "severityLevel": severity(record.get("level").and_then(Value::as_str).unwrap_or("")),
                "properties": properties,
            }},
        });
        let line = json_line(&envelope);
        let size = line.len();
        (line, size)
    });
    Batching::new(
        options.formatter,
        to_item,
        Box::new(sender),
        options.max_records,
        options.max_bytes,
    )
}

// ---- New Relic ----------------------------------------------------------------------------

/// Seconds since the Unix epoch, injectable so the 429 pause is testable.
pub type EpochClock = Arc<dyn Fn() -> f64 + Send + Sync>;

/// The real clock.
pub fn system_epoch_clock() -> EpochClock {
    Arc::new(|| {
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0.0, |d| d.as_secs_f64())
    })
}

/// The longest pause a server can ask for. A larger `Retry-After` is capped here, so one bad header
/// cannot silence a transport for good.
const LONGEST_RETRY_AFTER: f64 = 3600.0;

/// `Retry-After` as a number of seconds from `now`: whole seconds, or an HTTP date; anything else
/// (or a missing header, `inf`, `NaN`) waits 60 seconds, and nothing waits longer than an hour.
pub fn retry_after_seconds(value: Option<&str>, now: f64) -> f64 {
    let Some(text) = value.map(str::trim) else {
        return 60.0;
    };
    if let Ok(seconds) = text.parse::<f64>() {
        return if seconds.is_finite() {
            seconds.clamp(0.0, LONGEST_RETRY_AFTER)
        } else {
            60.0
        };
    }
    parse_http_date(text).map_or(60.0, |at| (at - now).clamp(0.0, LONGEST_RETRY_AFTER))
}

/// `Sun, 06 Nov 1994 08:49:37 GMT` as seconds since the epoch.
fn parse_http_date(text: &str) -> Option<f64> {
    let parts: Vec<&str> = text.split_whitespace().collect();
    let [_, day, month, year, time, zone] = parts.as_slice() else {
        return None;
    };
    if *zone != "GMT" {
        return None;
    }
    let months = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ];
    let month = months.iter().position(|m| m == month)? as i64 + 1;
    let (day, year): (i64, i64) = (day.parse().ok()?, year.parse().ok()?);
    let clock: Vec<i64> = time
        .split(':')
        .map(|p| p.parse().ok())
        .collect::<Option<_>>()?;
    let [hour, minute, second] = clock.as_slice() else {
        return None;
    };
    let y = if month <= 2 { year - 1 } else { year };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (if month > 2 { month - 3 } else { month + 9 }) + 2) / 5 + day - 1;
    let days = era * 146_097 + yoe * 365 + yoe / 4 - yoe / 100 + doy - 719_468;
    Some((days * 86_400 + hour * 3600 + minute * 60 + second) as f64)
}

struct NewRelic {
    poster: Box<dyn HttpPost>,
    url: String,
    license_key: String,
    timeout: Duration,
    clock: EpochClock,
    paused_until: f64,
}

impl BatchSender<String> for NewRelic {
    fn send(&mut self, batch: &[String]) -> Result<(), String> {
        let now = (self.clock)();
        if now < self.paused_until {
            return Err(format!(
                "NewRelicTransport: sends paused until {} after a 429 rate-limit response \u{2014} skipping this batch \
                 rather than making a doomed request",
                self.paused_until
            ));
        }
        let mut gz = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        gz.write_all(format!("[{}]", batch.join(",")).as_bytes())
            .map_err(|e| e.to_string())?;
        let body = gz.finish().map_err(|e| e.to_string())?;
        let headers = [
            header("Content-Type", "application/json"),
            header("Content-Encoding", "gzip"),
            header("Api-Key", &self.license_key),
        ];
        match self.poster.post(&self.url, &headers, &body, self.timeout) {
            Ok(r) if (200..300).contains(&r.status) => Ok(()),
            Ok(r) if r.status == 429 => {
                self.paused_until = now + retry_after_seconds(r.retry_after.as_deref(), now);
                Err(format!(
                    "NewRelicTransport: rate limited (HTTP 429); pausing sends until {}",
                    self.paused_until
                ))
            }
            Ok(r) => Err(format!(
                "NewRelicTransport: request to {} failed with status {} \u{2014} check the license key and region",
                self.url, r.status
            )),
            Err(error) => Err(format!("NewRelicTransport: request to {} failed \u{2014} {error}", self.url)),
        }
    }
}

/// New Relic log API for `region` (`US` or `EU`). `meta.eventType` is removed: New Relic reserves
/// it and drops the record otherwise. A 429 pauses sends for `Retry-After`.
pub fn new_relic_transport(
    license_key: &str,
    region: &str,
    options: HttpOptions,
    poster: Box<dyn HttpPost>,
    clock: EpochClock,
) -> Result<Batching<String>, String> {
    let url = match region {
        "US" => "https://log-api.newrelic.com/log/v1",
        "EU" => "https://log-api.eu.newrelic.com/log/v1",
        other => {
            return Err(format!(
                "NewRelicTransport: region must be 'US' or 'EU', got '{other}'"
            ))
        }
    };
    let sender = NewRelic {
        poster,
        url: url.to_string(),
        license_key: license_key.to_string(),
        timeout: options.timeout,
        clock,
        paused_until: 0.0,
    };
    let to_item: ToItem<String> = Box::new(|_, record| {
        let mut record = record.clone();
        if let Some(meta) = record.get_mut("meta").and_then(Value::as_object_mut) {
            meta.shift_remove("eventType");
        }
        let line = json_line(&record);
        let size = line.len();
        (line, size)
    });
    Ok(Batching::new(
        options.formatter,
        to_item,
        Box::new(sender),
        options.max_records,
        options.max_bytes,
    ))
}
