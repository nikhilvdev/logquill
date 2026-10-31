//! The `logquill._native` extension module: the Rust core, called from Python.
//!
//! This first slice exposes the pure helpers (formatting, parsing, canonical JSON, chain
//! verification). The `Logger` and the runtime come in later slices (`docs/stages/stage-5.md`).

mod alerting;
mod cloud;
mod convert;
mod dispatch;
mod metrics;
mod plugins;
mod state;
mod transports;

use logquill_core as core;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use serde_json::Value;

use convert::{to_py, to_value, NonFinite};

/// The current time as a record timestamp: UTC, `YYYY-MM-DDTHH:MM:SS.mmmZ`.
#[pyfunction]
fn utc_timestamp() -> String {
    use core::Clock;
    core::format_timestamp(core::SystemClock.now_unix_micros())
}

/// A new random Fernet key, in the form `FileTransport(encrypt_key=...)` accepts.
#[pyfunction]
fn generate_fernet_key() -> String {
    logquill_runtime::generate_fernet_key()
}

/// The canonical text of a JSON-like value: what the tamper chain hashes.
#[pyfunction]
fn canonical_json(value: &Bound<'_, PyAny>) -> PyResult<String> {
    Ok(core::canonical_json(&to_value(value, NonFinite::Error)?))
}

/// One record as a compact JSON line.
#[pyfunction]
fn format_json(record: &Bound<'_, PyAny>) -> PyResult<String> {
    Ok(core::format_json(&to_value(record, NonFinite::Null)?))
}

/// One record as a human-readable line, with `meta.stack` on the lines after.
#[pyfunction]
fn format_text(record: &Bound<'_, PyAny>) -> PyResult<String> {
    Ok(core::format_text(&to_value(record, NonFinite::Null)?))
}

/// One record as `key=value` pairs.
#[pyfunction]
fn format_logfmt(record: &Bound<'_, PyAny>) -> PyResult<String> {
    Ok(core::format_logfmt(&to_value(record, NonFinite::Null)?))
}

/// The fields of a logfmt line.
#[pyfunction]
fn parse_logfmt<'py>(py: Python<'py>, line: &str) -> PyResult<Bound<'py, PyAny>> {
    to_py(py, &Value::Object(core::parse_logfmt(line)))
}

/// Checks a record against the contract and returns it in current form; a 1.x record is read as
/// version 1.0. Raises `ValueError` saying what is wrong.
#[pyfunction]
fn parse_record<'py>(py: Python<'py>, raw: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    let record = core::parse_record(&to_value(raw, NonFinite::Null)?)
        .map_err(|e| PyValueError::new_err(e.to_string()))?;
    to_py(py, &record.to_value())
}

/// Verifies a hash-chained sequence of records. Returns `ok`, `records_checked`, `broken_at`,
/// `reason` and `head_hash`.
#[pyfunction]
#[pyo3(signature = (records, genesis = None))]
fn verify_chain<'py>(
    py: Python<'py>,
    records: &Bound<'py, PyAny>,
    genesis: Option<&str>,
) -> PyResult<Bound<'py, PyAny>> {
    // shortcut: the records are collected first, so memory is the size of the log; stream them
    // when verifying a file this large from Python matters (the `logquill verify` command already does).
    let mut collected = Vec::new();
    for record in records.try_iter()? {
        collected.push(to_value(&record?, NonFinite::Error)?);
    }
    let result = core::verify_chain(collected.iter(), genesis.unwrap_or(core::GENESIS_HASH));
    verification_dict(py, result)
}

/// A verification result as a dict: `ok`, `records_checked`, `broken_at`, `reason`, `head_hash`.
pub(crate) fn verification_dict(
    py: Python<'_>,
    result: core::Verification,
) -> PyResult<Bound<'_, PyAny>> {
    let out = PyDict::new(py);
    out.set_item("ok", result.ok)?;
    out.set_item("records_checked", result.records_checked)?;
    out.set_item("broken_at", result.broken_at)?;
    out.set_item("reason", result.reason)?;
    out.set_item("head_hash", result.head_hash)?;
    Ok(out.into_any())
}

#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add("GENESIS_HASH", core::GENESIS_HASH)?;
    module.add_class::<dispatch::PyDispatcher>()?;
    module.add_class::<dispatch::PyWorker>()?;
    module.add_class::<metrics::PyStatsd>()?;
    module.add_class::<metrics::PyPushgateway>()?;
    module.add_class::<alerting::PyAlerting>()?;
    module.add_class::<alerting::PyAlertSender>()?;
    module.add_function(wrap_pyfunction!(alerting::slack_sender, module)?)?;
    module.add_function(wrap_pyfunction!(alerting::pagerduty_sender, module)?)?;
    module.add_function(wrap_pyfunction!(alerting::email_sender, module)?)?;
    module.add_class::<state::PySampling>()?;
    module.add_class::<state::PyRateLimit>()?;
    module.add_class::<state::PyAdaptiveSampling>()?;
    module.add_class::<state::PyFlightRecorder>()?;
    module.add_class::<state::PyRunSummary>()?;
    module.add_class::<plugins::PyChain>()?;
    module.add_class::<plugins::PyKeyRedactor>()?;
    module.add_class::<plugins::PyPiiRedactor>()?;
    module.add("OFF_PLACEHOLDER", core::OFF_PLACEHOLDER)?;
    module.add("TRUNCATE_CHARS", core::TRUNCATE_CHARS)?;
    module.add("CYPHER", core::CYPHER)?;
    module.add("LEAF_PLACEHOLDER", core::LEAF_PLACEHOLDER)?;
    module.add_function(wrap_pyfunction!(plugins::to_ecs_document, module)?)?;
    module.add_function(wrap_pyfunction!(plugins::sql_row, module)?)?;
    module.add_function(wrap_pyfunction!(plugins::create_table_sql, module)?)?;
    module.add_function(wrap_pyfunction!(plugins::graph_row, module)?)?;
    module.add_function(wrap_pyfunction!(plugins::field_classes, module)?)?;
    module.add_function(wrap_pyfunction!(plugins::apply_policy_value, module)?)?;
    module.add_function(wrap_pyfunction!(plugins::sign_head, module)?)?;
    module.add_function(wrap_pyfunction!(plugins::verify_head_signature, module)?)?;
    module.add_function(wrap_pyfunction!(plugins::verify_signed_chain, module)?)?;
    module.add_class::<transports::NativeTransport>()?;
    module.add_function(wrap_pyfunction!(cloud::datadog, module)?)?;
    module.add_function(wrap_pyfunction!(cloud::new_relic, module)?)?;
    module.add_function(wrap_pyfunction!(cloud::elasticsearch, module)?)?;
    module.add_function(wrap_pyfunction!(cloud::app_insights, module)?)?;
    module.add_function(wrap_pyfunction!(cloud::syslog, module)?)?;
    module.add_function(wrap_pyfunction!(cloud::sqlite, module)?)?;
    module.add_function(wrap_pyfunction!(cloud::otlp_traces, module)?)?;
    module.add_function(wrap_pyfunction!(cloud::otlp_logs, module)?)?;
    module.add_function(wrap_pyfunction!(transports::console_transport, module)?)?;
    module.add_function(wrap_pyfunction!(transports::file_transport, module)?)?;
    module.add_function(wrap_pyfunction!(transports::http_transport, module)?)?;
    module.add_function(wrap_pyfunction!(convert::is_json_safe, module)?)?;
    module.add_function(wrap_pyfunction!(generate_fernet_key, module)?)?;
    module.add_function(wrap_pyfunction!(utc_timestamp, module)?)?;
    module.add_function(wrap_pyfunction!(canonical_json, module)?)?;
    module.add_function(wrap_pyfunction!(format_json, module)?)?;
    module.add_function(wrap_pyfunction!(format_text, module)?)?;
    module.add_function(wrap_pyfunction!(format_logfmt, module)?)?;
    module.add_function(wrap_pyfunction!(parse_logfmt, module)?)?;
    module.add_function(wrap_pyfunction!(parse_record, module)?)?;
    module.add_function(wrap_pyfunction!(verify_chain, module)?)?;
    Ok(())
}
