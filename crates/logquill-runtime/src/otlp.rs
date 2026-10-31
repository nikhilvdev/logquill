//! OpenTelemetry export over OTLP/HTTP with the JSON encoding, so no protobuf dependency is needed
//! (`docs/spec/03-privacy-tracing-otel.md`, "OTLPTransport" and "OTelLogsTransport").
//!
//! Spans come from records via the GenAI mapping in `logquill-core`; logs carry every record.
//! 64-bit integers are strings and ids are lower-case hex, as the OTLP JSON mapping requires.

use logquill_core::{
    epoch_ns, export_spec, json_line, log_attributes, span_id_for, trace_id_for,
    trace_id_from_parent, Convention, SpanKind, SpecOptions,
};
use serde_json::{json, Map, Number, Value};

use crate::batching::{BatchSender, Batching, ToItem};
use crate::http::HttpPost;
use crate::transport::Formatter;

/// Settings for the OTLP transports.
#[derive(Clone, Debug)]
pub struct OtlpOptions {
    /// Where to POST: `http://localhost:4318/v1/traces` or `/v1/logs` by default.
    pub endpoint: String,
    pub headers: Vec<(String, String)>,
    pub service_name: String,
    pub convention: Convention,
    pub default_provider: String,
    /// Export prompt and completion text (`meta.input_messages` / `output_messages`).
    pub capture_content: bool,
    pub max_records: usize,
    pub max_bytes: usize,
    pub timeout: std::time::Duration,
}

impl OtlpOptions {
    /// Defaults for traces (`/v1/traces`).
    pub fn traces() -> OtlpOptions {
        OtlpOptions::with_endpoint("http://localhost:4318/v1/traces")
    }

    /// Defaults for logs (`/v1/logs`).
    pub fn logs() -> OtlpOptions {
        OtlpOptions::with_endpoint("http://localhost:4318/v1/logs")
    }

    fn with_endpoint(endpoint: &str) -> OtlpOptions {
        OtlpOptions {
            endpoint: endpoint.to_string(),
            headers: Vec::new(),
            service_name: "logquill".into(),
            convention: Convention::Latest,
            default_provider: "unknown".into(),
            capture_content: false,
            max_records: 100,
            max_bytes: 1_000_000,
            timeout: std::time::Duration::from_secs(10),
        }
    }
}

fn double(x: f64) -> Value {
    Number::from_f64(x).map_or(Value::Null, Value::Number)
}

/// An attribute value in OTLP JSON. Scalars and same-typed lists pass through; anything else
/// becomes compact JSON text.
pub fn any_value(value: &Value) -> Value {
    match value {
        Value::String(s) => json!({"stringValue": s}),
        Value::Bool(b) => json!({"boolValue": b}),
        Value::Number(n) => {
            let text = n.to_string();
            if text.bytes().any(|b| matches!(b, b'.' | b'e' | b'E')) {
                match text.parse::<f64>() {
                    Ok(x) if x.is_finite() => json!({"doubleValue": double(x)}),
                    _ => json!({"stringValue": text}),
                }
            } else {
                json!({"intValue": text})
            }
        }
        Value::Array(items) if !items.is_empty() && same_scalar_kind(items) => {
            json!({"arrayValue": {"values": items.iter().map(any_value).collect::<Vec<_>>()}})
        }
        other => json!({"stringValue": json_line(other)}),
    }
}

fn same_scalar_kind(items: &[Value]) -> bool {
    let kind = |v: &Value| match v {
        Value::String(_) => Some(0),
        Value::Bool(_) => Some(1),
        Value::Number(n) if n.to_string().parse::<f64>().is_ok_and(f64::is_finite) => Some(2),
        _ => None,
    };
    kind(&items[0]).is_some() && items.iter().all(|v| kind(v) == kind(&items[0]))
}

fn attributes(map: &Map<String, Value>) -> Value {
    Value::Array(
        map.iter()
            .map(|(key, value)| json!({"key": key, "value": any_value(value)}))
            .collect(),
    )
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn random_hex(bytes: usize) -> String {
    let mut buffer = vec![0u8; bytes];
    getrandom::fill(&mut buffer).expect("the operating system has no random source");
    if buffer.iter().all(|b| *b == 0) {
        buffer[bytes - 1] = 1;
    }
    hex(&buffer)
}

fn now_ns() -> i128 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0, |d| d.as_nanos() as i128)
}

fn record_time(record: &Value) -> i128 {
    record
        .get("timestamp")
        .and_then(Value::as_str)
        .and_then(epoch_ns)
        .unwrap_or_else(now_ns)
}

fn resource(service_name: &str) -> String {
    json_line(
        &json!({"attributes": [{"key": "service.name", "value": {"stringValue": service_name}}]}),
    )
}

/// The OTLP span for a record, or `None` for a record that is not an LLM call, tool call or span.
pub fn span_json(record: &Value, options: &OtlpOptions) -> Option<Value> {
    let spec_options = SpecOptions {
        default_provider: options.default_provider.clone(),
        capture_content: options.capture_content,
    };
    let spec = export_spec(record, options.convention, &spec_options)?;
    let empty = Map::new();
    let meta = record
        .get("meta")
        .and_then(Value::as_object)
        .unwrap_or(&empty);
    let text = |key: &str| {
        meta.get(key)
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty())
    };
    let end = record_time(record);
    let start = match spec.duration_ms {
        Some(ms) if ms != 0.0 => end - (ms * 1e6) as i128,
        _ => end,
    };
    let span_id = text("span_id").map_or_else(|| random_hex(8), span_id_for);
    let parent = text("parent_span_id");
    let trace_id = trace_id_for(meta)
        .or_else(|| parent.map(trace_id_from_parent))
        .unwrap_or_else(|| random_hex(16));
    let mut span = json!({
        "traceId": trace_id,
        "spanId": span_id,
        "name": spec.name,
        "kind": match spec.kind { SpanKind::Client => 3, SpanKind::Internal => 1 },
        "startTimeUnixNano": start.to_string(),
        "endTimeUnixNano": end.to_string(),
        "attributes": attributes(&spec.attributes),
    });
    if let Some(parent) = parent {
        span["parentSpanId"] = json!(span_id_for(parent));
    }
    if !spec.events.is_empty() {
        span["events"] = Value::Array(
            spec.events
                .iter()
                .map(|(name, attrs)| json!({"timeUnixNano": end.to_string(), "name": name, "attributes": attributes(attrs)}))
                .collect(),
        );
    }
    if spec.is_error {
        let mut status = json!({"code": 2});
        if let Some(message) = meta.get("error").and_then(Value::as_str) {
            status["message"] = json!(message);
        }
        span["status"] = status;
    }
    Some(span)
}

fn severity_number(level: &str) -> u8 {
    match level {
        "TRACE" => 1,
        "DEBUG" => 5,
        "WARN" => 13,
        "ERROR" => 17,
        "FATAL" => 21,
        _ => 9,
    }
}

/// The OTLP log record for any record.
pub fn log_record_json(record: &Value, options: &OtlpOptions) -> Value {
    let time = record_time(record).to_string();
    let level = record
        .get("level")
        .and_then(Value::as_str)
        .unwrap_or("INFO");
    let mut attrs = Map::new();
    let empty = Map::new();
    let meta = record
        .get("meta")
        .and_then(Value::as_object)
        .unwrap_or(&empty);
    for (key, value) in meta {
        if value.is_null() {
            continue;
        }
        let name = if key == "stack" {
            "exception.stacktrace"
        } else {
            key.as_str()
        };
        attrs.insert(name.to_string(), value.clone());
    }
    attrs.insert(
        "logquill.logger".into(),
        record.get("logger").cloned().unwrap_or(Value::Null),
    );
    attrs.insert(
        "logquill.schema_version".into(),
        record
            .get("schema_version")
            .cloned()
            .unwrap_or_else(|| json!("1.0")),
    );
    for (key, value) in log_attributes(record, options.convention) {
        attrs.insert(key, value);
    }
    let mut log = json!({
        "timeUnixNano": time,
        "observedTimeUnixNano": time,
        "severityNumber": severity_number(level),
        "severityText": level,
        "body": {"stringValue": record.get("message").and_then(Value::as_str).unwrap_or("")},
        "attributes": attributes(&attrs),
    });
    let owner = meta
        .get("span_id")
        .and_then(Value::as_str)
        .filter(|s| !s.is_empty())
        .or_else(|| {
            meta.get("parent_span_id")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
        });
    if let Some(owner) = owner {
        let parent = meta
            .get("parent_span_id")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty());
        let trace = trace_id_for(meta).or_else(|| parent.map(trace_id_from_parent));
        if let Some(trace) = trace {
            log["traceId"] = json!(trace);
            log["spanId"] = json!(span_id_for(owner));
        }
    }
    log
}

struct OtlpSender {
    poster: Box<dyn HttpPost>,
    options: OtlpOptions,
    /// `resourceSpans` + `scopeSpans` + `spans`, or `resourceLogs` + `scopeLogs` + `logRecords`.
    names: (&'static str, &'static str, &'static str),
    label: &'static str,
}

impl BatchSender<String> for OtlpSender {
    fn send(&mut self, batch: &[String]) -> Result<(), String> {
        let items: Vec<&str> = batch
            .iter()
            .map(String::as_str)
            .filter(|s| !s.is_empty())
            .collect();
        if items.is_empty() {
            return Ok(());
        }
        let (outer, scope, inner) = self.names;
        let body = format!(
            r#"{{"{outer}":[{{"resource":{},"{scope}":[{{"scope":{{"name":"logquill"}},"{inner}":[{}]}}]}}]}}"#,
            resource(&self.options.service_name),
            items.join(",")
        );
        let mut headers = vec![("Content-Type".to_string(), "application/json".to_string())];
        headers.extend(self.options.headers.iter().cloned());
        let url = &self.options.endpoint;
        match self.poster.post(url, &headers, body.as_bytes(), self.options.timeout) {
            Ok(r) if (200..300).contains(&r.status) => Ok(()),
            Ok(r) => Err(format!(
                "{}: request to {url} failed with status {} \u{2014} check the collector URL and headers",
                self.label, r.status
            )),
            Err(error) => Err(format!("{}: request to {url} failed \u{2014} {error}", self.label)),
        }
    }
}

/// Exports `span()` blocks, tool actions and LLM calls as OTLP spans. The formatter is ignored.
pub fn otlp_trace_transport(options: OtlpOptions, poster: Box<dyn HttpPost>) -> Batching<String> {
    let for_items = options.clone();
    let (records, bytes) = (options.max_records, options.max_bytes);
    let sender = OtlpSender {
        poster,
        options,
        names: ("resourceSpans", "scopeSpans", "spans"),
        label: "OTLPTransport",
    };
    let to_item: ToItem<String> = Box::new(move |_, record| {
        let line = span_json(record, &for_items)
            .map(|s| json_line(&s))
            .unwrap_or_default();
        let size = line.len();
        (line, size)
    });
    Batching::new(Formatter::Json, to_item, Box::new(sender), records, bytes)
}

/// Exports every record as an OTLP log. The formatter is ignored.
pub fn otlp_log_transport(options: OtlpOptions, poster: Box<dyn HttpPost>) -> Batching<String> {
    let for_items = options.clone();
    let (records, bytes) = (options.max_records, options.max_bytes);
    let sender = OtlpSender {
        poster,
        options,
        names: ("resourceLogs", "scopeLogs", "logRecords"),
        label: "OTelLogsTransport",
    };
    let to_item: ToItem<String> = Box::new(move |_, record| {
        let line = json_line(&log_record_json(record, &for_items));
        let size = line.len();
        (line, size)
    });
    Batching::new(Formatter::Json, to_item, Box::new(sender), records, bytes)
}
