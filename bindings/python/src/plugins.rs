//! The core's plugin logic as Python classes: redaction, PII scanning and the tamper chain.

use std::sync::Arc;

use logquill_core as core;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use serde_json::Value;

use crate::convert::{to_py, to_value, NonFinite};

fn meta_of(value: Value) -> PyResult<serde_json::Map<String, Value>> {
    match value {
        Value::Object(map) => Ok(map),
        _ => Err(PyValueError::new_err("meta must be a mapping")),
    }
}

/// `record` with Elastic Common Schema fields added alongside its own.
#[pyfunction]
pub fn to_ecs_document<'py>(
    py: Python<'py>,
    record: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    to_py(
        py,
        &core::to_ecs_document(&to_value(record, NonFinite::Null)?),
    )
}

/// The columns of one SQL log row, in table order (`llm` is `None` without an LLM block).
#[pyfunction]
pub fn sql_row<'py>(py: Python<'py>, record: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyAny>> {
    to_py(py, &core::sql_row(&to_value(record, NonFinite::Null)?))
}

/// `CREATE TABLE IF NOT EXISTS` for the log table in `dialect` (`sqlite`, `postgres` or `mysql`).
#[pyfunction]
pub fn create_table_sql(dialect: &str, table: &str) -> PyResult<String> {
    let dialect = match dialect {
        "sqlite" => core::Dialect::Sqlite,
        "postgres" => core::Dialect::Postgres,
        "mysql" => core::Dialect::Mysql,
        other => {
            return Err(PyValueError::new_err(format!(
                "dialect must be 'sqlite', 'postgres' or 'mysql', got '{other}'"
            )))
        }
    };
    Ok(core::create_table_sql(dialect, table))
}

/// One record as a graph node: its key, properties, session and parent.
#[pyfunction]
#[pyo3(signature = (record, run_key = "run_id", trace_key = "trace_id"))]
pub fn graph_row<'py>(
    py: Python<'py>,
    record: &Bound<'py, PyAny>,
    run_key: &str,
    trace_key: &str,
) -> PyResult<Bound<'py, PyAny>> {
    let keys = core::GraphKeys {
        run_key: run_key.to_string(),
        trace_key: trace_key.to_string(),
    };
    to_py(
        py,
        &core::graph_row(&to_value(record, NonFinite::Null)?, &keys),
    )
}

/// The well-known `meta` keys and their class (`secret`, `pii` or `content`), in registry order.
#[pyfunction]
pub fn field_classes() -> Vec<(&'static str, &'static str)> {
    core::FIELD_CLASSES
        .iter()
        .map(|(key, class)| (*key, class.name()))
        .collect()
}

/// `value` as the content-capture policy `policy` (`off`, `hash`, `truncate` or `full`) leaves it.
#[pyfunction]
pub fn apply_policy_value<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    policy: &str,
) -> PyResult<Bound<'py, PyAny>> {
    let policy =
        core::ContentPolicy::parse(policy).map_err(|e| PyValueError::new_err(e.to_string()))?;
    to_py(py, &policy.apply(&to_value(value, NonFinite::Null)?))
}

/// Redacts `meta` values by key, case-insensitively, at every depth.
#[pyclass(name = "KeyRedactor", frozen)]
pub struct PyKeyRedactor {
    inner: core::KeyRedactor,
}

#[pymethods]
impl PyKeyRedactor {
    #[new]
    #[pyo3(signature = (keys, replacement = "***"))]
    fn new(keys: Vec<String>, replacement: &str) -> PyKeyRedactor {
        PyKeyRedactor {
            inner: core::KeyRedactor::new(keys.iter().map(String::as_str), &[], replacement),
        }
    }

    /// A copy of `meta` with the matching values replaced.
    fn redact_meta<'py>(
        &self,
        py: Python<'py>,
        meta: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let mut map = meta_of(to_value(meta, NonFinite::Null)?)?;
        self.inner.redact_meta(&mut map);
        to_py(py, &Value::Object(map))
    }

    /// The text to show for a local variable: the replacement if its name matches.
    fn redact_local(&self, name: &str, text: &str) -> String {
        self.inner.redact_local(name, text)
    }
}

/// Redacts PII- and secret-shaped text anywhere in `meta`.
#[pyclass(name = "PiiRedactor", frozen)]
pub struct PyPiiRedactor {
    inner: core::PiiRedactor,
}

#[pymethods]
impl PyPiiRedactor {
    #[new]
    #[pyo3(signature = (replacement = "***"))]
    fn new(replacement: &str) -> PyPiiRedactor {
        PyPiiRedactor {
            inner: core::PiiRedactor::new(replacement),
        }
    }

    /// A copy of `meta` with matches in every string, at every depth, replaced.
    fn redact_meta<'py>(
        &self,
        py: Python<'py>,
        meta: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let mut map = meta_of(to_value(meta, NonFinite::Null)?)?;
        self.inner.redact_meta(&mut map);
        to_py(py, &Value::Object(map))
    }

    /// `text` with matches replaced.
    fn redact_text(&self, text: &str) -> String {
        self.inner.redact_text(text)
    }
}

/// A hash chain that several writers share.
#[pyclass(name = "Chain", frozen)]
pub struct PyChain {
    pub(crate) inner: Arc<core::SharedChain>,
}

#[pymethods]
impl PyChain {
    #[new]
    #[pyo3(signature = (genesis = None))]
    fn new(genesis: Option<&str>) -> PyChain {
        let chain = genesis.map_or_else(core::Chain::default, core::Chain::new);
        PyChain {
            inner: Arc::new(core::SharedChain::new(chain)),
        }
    }

    /// The hash of the last record chained (the genesis hash before any).
    fn head(&self) -> String {
        self.inner.head()
    }

    /// HMAC-SHA256 of the head under `key`, hex-encoded.
    fn sign_head(&self, key: &[u8]) -> String {
        core::sign_head(&self.inner.head(), key)
    }

    /// Chains `record` now and returns it with `meta.prev_hash` and `meta.hash` set. For loggers
    /// with no transports; otherwise the dispatcher chains each record as it writes it.
    fn stamp<'py>(
        &self,
        py: Python<'py>,
        record: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let value = to_value(record, NonFinite::Error)?;
        let stamped = self.inner.write(value, Value::clone);
        to_py(py, &stamped)
    }
}

/// HMAC-SHA256 of `head_hash` under `key`, hex-encoded.
#[pyfunction]
pub fn sign_head(head_hash: &str, key: &[u8]) -> String {
    core::sign_head(head_hash, key)
}

/// Whether `signature` is what `sign_head(head_hash, key)` gives (compared in constant time).
#[pyfunction]
pub fn verify_head_signature(head_hash: &str, key: &[u8], signature: &str) -> bool {
    core::verify_head_signature(head_hash, key, signature)
}

/// Verifies a chain and that its head matches `signature`.
#[pyfunction]
#[pyo3(signature = (records, key, signature, genesis = None))]
pub fn verify_signed_chain<'py>(
    py: Python<'py>,
    records: &Bound<'py, PyAny>,
    key: &Bound<'py, PyBytes>,
    signature: &str,
    genesis: Option<&str>,
) -> PyResult<Bound<'py, PyAny>> {
    let mut collected = Vec::new();
    for record in records.try_iter()? {
        collected.push(to_value(&record?, NonFinite::Error)?);
    }
    let result = core::verify_signed_chain(
        collected.iter(),
        key.as_bytes(),
        signature,
        genesis.unwrap_or(core::GENESIS_HASH),
    );
    crate::verification_dict(py, result)
}
