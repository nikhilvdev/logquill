//! Native constructors for the HTTP cloud sinks, syslog, SQLite and OpenTelemetry (OTLP/HTTP, JSON
//! encoding): the transports whose whole job is I/O, done in Rust with no GIL held.

use std::time::Duration;

use logquill_core::{resolve_convention, Convention};
use logquill_runtime::{
    app_insights_transport, datadog_transport, elasticsearch_transport, new_relic_transport,
    otlp_log_transport, otlp_trace_transport, sqlite_transport, system_epoch_clock, HttpOptions,
    OtlpOptions, SqliteOptions, SyslogOptions, SyslogProtocol, SyslogTransport, UreqPost,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::transports::{formatter, NativeTransport};

fn http(max_records: usize, max_bytes: usize, timeout: f64) -> HttpOptions {
    HttpOptions {
        max_records,
        max_bytes,
        timeout: Duration::from_secs_f64(timeout),
        ..HttpOptions::default()
    }
}

/// Datadog log intake for `site`. Records are sent as JSON objects whatever the formatter.
#[pyfunction]
#[pyo3(signature = (api_key, site = "datadoghq.com", max_records = 100, max_bytes = 1_000_000, timeout = 10.0))]
pub fn datadog(
    api_key: &str,
    site: &str,
    max_records: usize,
    max_bytes: usize,
    timeout: f64,
) -> NativeTransport {
    NativeTransport::new(datadog_transport(
        api_key,
        site,
        http(max_records, max_bytes, timeout),
        Box::new(UreqPost),
    ))
}

/// New Relic log API for `region` (`US` or `EU`), gzip-compressed, pausing for `Retry-After` after
/// a 429.
#[pyfunction]
#[pyo3(signature = (license_key, region = "US", max_records = 100, max_bytes = 1_000_000, timeout = 10.0))]
pub fn new_relic(
    license_key: &str,
    region: &str,
    max_records: usize,
    max_bytes: usize,
    timeout: f64,
) -> PyResult<NativeTransport> {
    new_relic_transport(
        license_key,
        region,
        http(max_records, max_bytes, timeout),
        Box::new(UreqPost),
        system_epoch_clock(),
    )
    .map(NativeTransport::new)
    .map_err(PyValueError::new_err)
}

/// Elasticsearch `_bulk` with Elastic Common Schema documents.
#[pyfunction]
#[pyo3(signature = (url, index = "logs", max_records = 100, max_bytes = 1_000_000, timeout = 10.0))]
pub fn elasticsearch(
    url: &str,
    index: &str,
    max_records: usize,
    max_bytes: usize,
    timeout: f64,
) -> NativeTransport {
    NativeTransport::new(elasticsearch_transport(
        url,
        index,
        http(max_records, max_bytes, timeout),
        Box::new(UreqPost),
    ))
}

/// Azure Application Insights trace envelopes.
#[pyfunction]
#[pyo3(signature = (instrumentation_key, max_records = 100, max_bytes = 1_000_000, timeout = 10.0))]
pub fn app_insights(
    instrumentation_key: &str,
    max_records: usize,
    max_bytes: usize,
    timeout: f64,
) -> NativeTransport {
    NativeTransport::new(app_insights_transport(
        instrumentation_key,
        http(max_records, max_bytes, timeout),
        Box::new(UreqPost),
    ))
}

/// Syslog (RFC 5424) over UDP or TCP.
#[pyfunction]
#[pyo3(signature = (host = "localhost", port = 514, protocol = "udp", facility = 1, app_name = None, formatter_name = "json"))]
pub fn syslog(
    host: &str,
    port: u16,
    protocol: &str,
    facility: u8,
    app_name: Option<String>,
    formatter_name: &str,
) -> PyResult<NativeTransport> {
    Ok(NativeTransport::new(SyslogTransport::new(SyslogOptions {
        host: host.to_string(),
        port,
        protocol: SyslogProtocol::parse(protocol).map_err(PyValueError::new_err)?,
        facility,
        app_name,
        formatter: formatter(formatter_name)?,
        ..SyslogOptions::default()
    })))
}

/// A SQLite table of log rows, written in batches.
#[pyfunction]
#[pyo3(signature = (filename = ":memory:", table_name = "logs", ensure_schema = false, max_records = 100, max_bytes = 1_000_000))]
pub fn sqlite(
    filename: &str,
    table_name: &str,
    ensure_schema: bool,
    max_records: usize,
    max_bytes: usize,
) -> PyResult<NativeTransport> {
    sqlite_transport(SqliteOptions {
        filename: filename.to_string(),
        table_name: table_name.to_string(),
        ensure_schema,
        max_records,
        max_bytes,
    })
    .map(NativeTransport::new)
    .map_err(PyValueError::new_err)
}

#[allow(clippy::too_many_arguments)]
fn otlp_options(
    defaults: OtlpOptions,
    endpoint: Option<&str>,
    headers: Vec<(String, String)>,
    service_name: &str,
    semconv: Option<&str>,
    default_provider: &str,
    capture_content: bool,
    max_records: usize,
    max_bytes: usize,
    timeout: f64,
) -> PyResult<OtlpOptions> {
    let convention: Convention = resolve_convention(semconv).map_err(PyValueError::new_err)?;
    Ok(OtlpOptions {
        endpoint: endpoint.map_or(defaults.endpoint, str::to_string),
        headers,
        service_name: service_name.to_string(),
        convention,
        default_provider: default_provider.to_string(),
        capture_content,
        max_records,
        max_bytes,
        timeout: Duration::from_secs_f64(timeout),
    })
}

/// LLM calls, tool calls and spans as OpenTelemetry spans, over OTLP/HTTP JSON.
#[pyfunction]
#[pyo3(signature = (endpoint = None, headers = Vec::new(), service_name = "logquill", semconv = None, default_provider = "unknown", capture_content = false, max_records = 100, max_bytes = 1_000_000, timeout = 10.0))]
#[allow(clippy::too_many_arguments)]
pub fn otlp_traces(
    endpoint: Option<&str>,
    headers: Vec<(String, String)>,
    service_name: &str,
    semconv: Option<&str>,
    default_provider: &str,
    capture_content: bool,
    max_records: usize,
    max_bytes: usize,
    timeout: f64,
) -> PyResult<NativeTransport> {
    let options = otlp_options(
        OtlpOptions::traces(),
        endpoint,
        headers,
        service_name,
        semconv,
        default_provider,
        capture_content,
        max_records,
        max_bytes,
        timeout,
    )?;
    Ok(NativeTransport::new(otlp_trace_transport(
        options,
        Box::new(UreqPost),
    )))
}

/// Every record as an OpenTelemetry log, over OTLP/HTTP JSON.
#[pyfunction]
#[pyo3(signature = (endpoint = None, headers = Vec::new(), service_name = "logquill", semconv = None, default_provider = "unknown", capture_content = false, max_records = 100, max_bytes = 1_000_000, timeout = 10.0))]
#[allow(clippy::too_many_arguments)]
pub fn otlp_logs(
    endpoint: Option<&str>,
    headers: Vec<(String, String)>,
    service_name: &str,
    semconv: Option<&str>,
    default_provider: &str,
    capture_content: bool,
    max_records: usize,
    max_bytes: usize,
    timeout: f64,
) -> PyResult<NativeTransport> {
    let options = otlp_options(
        OtlpOptions::logs(),
        endpoint,
        headers,
        service_name,
        semconv,
        default_provider,
        capture_content,
        max_records,
        max_bytes,
        timeout,
    )?;
    Ok(NativeTransport::new(otlp_log_transport(
        options,
        Box::new(UreqPost),
    )))
}
