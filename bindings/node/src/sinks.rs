//! The core runtime's batching sinks for Node: the four HTTP cloud sinks, syslog, SQLite and OTLP
//! (traces and logs). Each returns a `NativeTransport`, so a `Logger` hands it records on the Rust worker
//! thread; the SDK wraps them as transports.

use std::collections::HashMap;
use std::time::Duration;

use logquill_core::resolve_convention;
use logquill_runtime::{
    app_insights_transport, datadog_transport, elasticsearch_transport, new_relic_transport,
    otlp_log_transport, otlp_trace_transport, sqlite_transport, system_epoch_clock, HttpOptions,
    OtlpOptions, SqliteOptions, SyslogOptions, SyslogProtocol, SyslogTransport, UreqPost,
};
use napi::{Error, Result};
use napi_derive::napi;

use crate::dispatch::{formatter, NativeTransport};

fn reason(message: impl Into<String>) -> Error {
    Error::from_reason(message.into())
}

/// Batch bounds shared by the HTTP sinks; every field is optional.
#[napi(object)]
pub struct BatchConfig {
    /// Send a batch once it holds this many records (default 100).
    pub max_records: Option<u32>,
    /// ...or this many bytes (default 1,000,000).
    pub max_bytes: Option<f64>,
    /// Seconds to wait for the endpoint (default 10).
    pub timeout: Option<f64>,
}

fn http(batch: &Option<BatchConfig>) -> HttpOptions {
    let defaults = HttpOptions::default();
    let (records, bytes, timeout) = match batch {
        Some(b) => (b.max_records, b.max_bytes, b.timeout),
        None => (None, None, None),
    };
    HttpOptions {
        max_records: records.map_or(100, |n| n as usize),
        max_bytes: bytes.map_or(1_000_000, |n| n.max(0.0) as usize),
        timeout: timeout.map_or(Duration::from_secs(10), |s| {
            Duration::from_secs_f64(s.max(0.0))
        }),
        ..defaults
    }
}

/// Datadog Logs (`site` defaults to `datadoghq.com`).
#[napi]
pub fn datadog(
    api_key: String,
    site: Option<String>,
    batch: Option<BatchConfig>,
) -> NativeTransport {
    NativeTransport::new(datadog_transport(
        &api_key,
        site.as_deref().unwrap_or("datadoghq.com"),
        http(&batch),
        Box::new(UreqPost),
    ))
}

/// New Relic Logs; `region` is `US` (default) or `EU`.
#[napi]
pub fn new_relic(
    license_key: String,
    region: Option<String>,
    batch: Option<BatchConfig>,
) -> Result<NativeTransport> {
    new_relic_transport(
        &license_key,
        region.as_deref().unwrap_or("US"),
        http(&batch),
        Box::new(UreqPost),
        system_epoch_clock(),
    )
    .map(NativeTransport::new)
    .map_err(reason)
}

/// Elasticsearch bulk indexing of ECS documents (`index` defaults to `logs`).
#[napi]
pub fn elasticsearch(
    url: String,
    index: Option<String>,
    batch: Option<BatchConfig>,
) -> NativeTransport {
    NativeTransport::new(elasticsearch_transport(
        &url,
        index.as_deref().unwrap_or("logs"),
        http(&batch),
        Box::new(UreqPost),
    ))
}

/// Azure Application Insights.
#[napi]
pub fn app_insights(instrumentation_key: String, batch: Option<BatchConfig>) -> NativeTransport {
    NativeTransport::new(app_insights_transport(
        &instrumentation_key,
        http(&batch),
        Box::new(UreqPost),
    ))
}

/// Options for `syslog`; every field is optional.
#[napi(object)]
pub struct SyslogConfig {
    /// Default `localhost`.
    pub host: Option<String>,
    /// Default 514.
    pub port: Option<u32>,
    /// `"udp"` (default) or `"tcp"`.
    pub protocol: Option<String>,
    /// Syslog facility number (default 1, user-level).
    pub facility: Option<u32>,
    pub app_name: Option<String>,
    pub formatter: Option<String>,
}

/// RFC 5424 syslog over UDP or TCP.
#[napi]
pub fn syslog(config: Option<SyslogConfig>) -> Result<NativeTransport> {
    let c = config.unwrap_or(SyslogConfig {
        host: None,
        port: None,
        protocol: None,
        facility: None,
        app_name: None,
        formatter: None,
    });
    Ok(NativeTransport::new(SyslogTransport::new(SyslogOptions {
        host: c.host.unwrap_or_else(|| "localhost".to_string()),
        port: c.port.map_or(514, |p| p as u16),
        protocol: SyslogProtocol::parse(c.protocol.as_deref().unwrap_or("udp")).map_err(reason)?,
        facility: c.facility.map_or(1, |f| f as u8),
        app_name: c.app_name,
        formatter: formatter(c.formatter)?,
        ..SyslogOptions::default()
    })))
}

/// Options for `sqlite`; every field is optional.
#[napi(object)]
pub struct SqliteConfig {
    /// Default `":memory:"`.
    pub filename: Option<String>,
    /// Default `logs`; validated as an SQL identifier.
    pub table_name: Option<String>,
    /// Create the table if it is missing (development only; migrations are the application's job).
    pub ensure_schema: Option<bool>,
    pub max_records: Option<u32>,
    pub max_bytes: Option<f64>,
}

/// Batched inserts into a SQLite database (also what the local viewer reads).
#[napi]
pub fn sqlite(config: Option<SqliteConfig>) -> Result<NativeTransport> {
    let c = config.unwrap_or(SqliteConfig {
        filename: None,
        table_name: None,
        ensure_schema: None,
        max_records: None,
        max_bytes: None,
    });
    sqlite_transport(SqliteOptions {
        filename: c.filename.unwrap_or_else(|| ":memory:".to_string()),
        table_name: c.table_name.unwrap_or_else(|| "logs".to_string()),
        ensure_schema: c.ensure_schema.unwrap_or(false),
        max_records: c.max_records.map_or(100, |n| n as usize),
        max_bytes: c.max_bytes.map_or(1_000_000, |n| n.max(0.0) as usize),
    })
    .map(NativeTransport::new)
    .map_err(reason)
}

/// Options for the OTLP sinks; every field is optional.
#[napi(object)]
pub struct OtlpConfig {
    /// Default `http://localhost:4318/v1/traces` (or `/v1/logs`).
    pub endpoint: Option<String>,
    pub headers: Option<HashMap<String, String>>,
    /// The `service.name` resource attribute (default `logquill`).
    pub service_name: Option<String>,
    /// `"latest"` or `"legacy"` GenAI attribute names; unset reads `OTEL_SEMCONV_STABILITY_OPT_IN`.
    pub semconv: Option<String>,
    pub default_provider: Option<String>,
    /// Add prompt and completion text (the content policy still applies first).
    pub capture_content: Option<bool>,
    pub max_records: Option<u32>,
    pub max_bytes: Option<f64>,
    pub timeout: Option<f64>,
}

fn otlp_options(defaults: OtlpOptions, config: Option<OtlpConfig>) -> Result<OtlpOptions> {
    let Some(c) = config else {
        return Ok(defaults);
    };
    let mut headers: Vec<(String, String)> = c.headers.unwrap_or_default().into_iter().collect();
    headers.sort();
    Ok(OtlpOptions {
        endpoint: c.endpoint.unwrap_or(defaults.endpoint),
        headers,
        service_name: c.service_name.unwrap_or_else(|| "logquill".to_string()),
        convention: resolve_convention(c.semconv.as_deref()).map_err(reason)?,
        default_provider: c.default_provider.unwrap_or_else(|| "unknown".to_string()),
        capture_content: c.capture_content.unwrap_or(false),
        max_records: c.max_records.map_or(100, |n| n as usize),
        max_bytes: c.max_bytes.map_or(1_000_000, |n| n.max(0.0) as usize),
        timeout: Duration::from_secs_f64(c.timeout.unwrap_or(10.0).max(0.0)),
    })
}

/// Spans (`invoke_agent`, `execute_tool`, `chat`) to an OTLP/HTTP traces endpoint.
#[napi]
pub fn otlp_traces(config: Option<OtlpConfig>) -> Result<NativeTransport> {
    Ok(NativeTransport::new(otlp_trace_transport(
        otlp_options(OtlpOptions::traces(), config)?,
        Box::new(UreqPost),
    )))
}

/// Every record as an OTLP log to an OTLP/HTTP logs endpoint.
#[napi]
pub fn otlp_logs(config: Option<OtlpConfig>) -> Result<NativeTransport> {
    Ok(NativeTransport::new(otlp_log_transport(
        otlp_options(OtlpOptions::logs(), config)?,
        Box::new(UreqPost),
    )))
}
