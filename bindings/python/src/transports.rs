//! Transports for the Rust dispatcher: the native ones from `logquill-runtime`, and a bridge that
//! lets a Python object with `format` / `write` / `flush` / `close` act as a transport.
//!
//! Every transport runs on the dispatcher's worker thread. A native one never touches the GIL; a
//! Python one takes it only for the duration of each call.

use std::io::{self, Write};
use std::sync::{Arc, Mutex};

use logquill_runtime::{
    Compression, Console, FileOptions, FileTransport, Formatter, HttpOptions, Transport, UreqPost,
};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use serde_json::Value;

use crate::convert::{to_py, to_value, NonFinite};

pub type Shared = Arc<Mutex<Box<dyn Transport>>>;

/// A transport implemented in Rust. One instance can serve several loggers; closing it from any
/// of them closes it for all, as with the Python transports.
#[pyclass(name = "NativeTransport", frozen)]
pub struct NativeTransport {
    pub(crate) inner: Shared,
}

impl NativeTransport {
    pub(crate) fn new(transport: impl Transport + 'static) -> NativeTransport {
        NativeTransport {
            inner: Arc::new(Mutex::new(Box::new(transport))),
        }
    }
}

#[pymethods]
impl NativeTransport {
    /// The line this transport writes for `record`.
    fn format(&self, record: &Bound<'_, PyAny>) -> PyResult<String> {
        let value = to_value(record, NonFinite::Null)?;
        Ok(locked(&self.inner).format(&value))
    }

    /// Writes an already-formatted line (and the record, for transports that need its fields).
    fn write(&self, py: Python<'_>, formatted: &str, record: &Bound<'_, PyAny>) -> PyResult<()> {
        let value = to_value(record, NonFinite::Null)?;
        let inner = self.inner.clone();
        let formatted = formatted.to_string();
        py.detach(move || locked(&inner).write(&formatted, &value))
            .map_err(PyRuntimeError::new_err)
    }

    /// Sends whatever the transport buffers.
    fn flush(&self, py: Python<'_>) -> PyResult<()> {
        let inner = self.inner.clone();
        py.detach(move || locked(&inner).flush())
            .map_err(PyRuntimeError::new_err)
    }

    /// Flushes and releases the transport.
    fn close(&self, py: Python<'_>) -> PyResult<()> {
        let inner = self.inner.clone();
        py.detach(move || locked(&inner).close())
            .map_err(PyRuntimeError::new_err)
    }
}

/// What the dispatcher holds for a [`NativeTransport`]: a handle onto the shared instance.
pub struct SharedTransport(pub Shared);

fn locked(shared: &Shared) -> std::sync::MutexGuard<'_, Box<dyn Transport>> {
    shared.lock().unwrap_or_else(|p| p.into_inner())
}

impl Transport for SharedTransport {
    fn format(&self, record: &Value) -> String {
        locked(&self.0).format(record)
    }
    fn write(&mut self, formatted: &str, record: &Value) -> Result<(), String> {
        locked(&self.0).write(formatted, record)
    }
    fn flush(&mut self) -> Result<(), String> {
        locked(&self.0).flush()
    }
    fn close(&mut self) -> Result<(), String> {
        locked(&self.0).close()
    }
}

fn error_text(error: &PyErr) -> String {
    Python::attach(|py| {
        let kind = error.get_type(py).name().map(|n| n.to_string());
        let message = error.value(py).to_string();
        match kind {
            Ok(kind) => format!("{kind}: {message}"),
            Err(_) => message,
        }
    })
}

/// A Python object used as a transport.
pub struct HostTransport {
    object: Py<PyAny>,
    /// `Transport::format` cannot fail, so a failure waits here for `write` to report it.
    format_error: Mutex<Option<String>>,
    /// The Python form of the record `format` just converted, with that record's address, kept for
    /// the `write` that follows with the same record: one conversion per record, and `format` and
    /// `write` see the same dict, as they did in 2.0.0.
    converted: Mutex<Option<(usize, Py<PyAny>)>>,
}

fn address(record: &Value) -> usize {
    record as *const Value as usize
}

impl HostTransport {
    pub fn new(object: Py<PyAny>) -> HostTransport {
        HostTransport {
            object,
            format_error: Mutex::new(None),
            converted: Mutex::new(None),
        }
    }

    fn call(&self, method: &str, optional: bool) -> Result<(), String> {
        Python::attach(|py| {
            let object = self.object.bind(py);
            if optional && !object.hasattr(method)? {
                return Ok(());
            }
            object.call_method0(method).map(|_| ())
        })
        .map_err(|e| error_text(&e))
    }
}

impl Transport for HostTransport {
    fn format(&self, record: &Value) -> String {
        let mut converted = self.converted.lock().unwrap_or_else(|p| p.into_inner());
        *converted = None;
        let formatted = Python::attach(|py| -> PyResult<String> {
            let py_record = to_py(py, record)?;
            let text = self
                .object
                .bind(py)
                .call_method1("format", (&py_record,))?
                .extract()?;
            *converted = Some((address(record), py_record.unbind()));
            Ok(text)
        });
        formatted.unwrap_or_else(|e| {
            *self.format_error.lock().unwrap_or_else(|p| p.into_inner()) = Some(error_text(&e));
            String::new()
        })
    }

    fn write(&mut self, formatted: &str, record: &Value) -> Result<(), String> {
        if let Some(error) = self
            .format_error
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .take()
        {
            return Err(error);
        }
        let converted = self
            .converted
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .take()
            .filter(|(at, _)| *at == address(record));
        Python::attach(|py| {
            let record = match converted {
                Some((_, object)) => object.into_bound(py),
                None => to_py(py, record)?,
            };
            self.object
                .bind(py)
                .call_method1("write", (formatted, record))
                .map(|_| ())
        })
        .map_err(|e| error_text(&e))
    }

    fn flush(&mut self) -> Result<(), String> {
        self.call("flush", true)
    }

    fn close(&mut self) -> Result<(), String> {
        self.call("close", true)
    }
}

/// `sys.stdout` or `sys.stderr` looked up at each write (so a test's capture or a notebook's
/// redirection is honoured), or a stream the caller supplied.
struct PyWriter {
    target: Option<Py<PyAny>>,
    stream: &'static str,
}

impl PyWriter {
    fn with_stream<R>(&self, f: impl FnOnce(&Bound<'_, PyAny>) -> PyResult<R>) -> io::Result<R> {
        Python::attach(|py| {
            let stream = match &self.target {
                Some(target) => target.bind(py).clone(),
                None => py.import("sys")?.getattr(self.stream)?,
            };
            f(&stream)
        })
        .map_err(io::Error::other)
    }
}

impl Write for PyWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let text = String::from_utf8_lossy(bytes).into_owned();
        self.with_stream(|stream| stream.call_method1("write", (text,)).map(|_| ()))?;
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        self.with_stream(|stream| stream.call_method0("flush").map(|_| ()))
    }
}

pub(crate) fn formatter(name: &str) -> PyResult<Formatter> {
    Formatter::parse(name).map_err(PyValueError::new_err)
}

/// Writes INFO and below to stdout and ERROR and above to stderr (or to the streams given).
#[pyfunction]
#[pyo3(signature = (colour, formatter_name = "json", stdout = None, stderr = None))]
pub fn console_transport(
    colour: bool,
    formatter_name: &str,
    stdout: Option<Py<PyAny>>,
    stderr: Option<Py<PyAny>>,
) -> PyResult<NativeTransport> {
    let out = PyWriter {
        target: stdout,
        stream: "stdout",
    };
    let err = PyWriter {
        target: stderr,
        stream: "stderr",
    };
    Ok(NativeTransport::new(Console::with_streams(
        Box::new(out),
        Box::new(err),
        colour,
        formatter(formatter_name)?,
    )))
}

/// Appends records to a file, rotating by size; optionally compressing rotated files and
/// encrypting each line with a Fernet key.
#[pyfunction]
#[pyo3(signature = (path, max_bytes = 10 * 1024 * 1024, backup_count = 5, formatter_name = "json", compression = None, encrypt_key = None))]
pub fn file_transport(
    path: &str,
    max_bytes: u64,
    backup_count: usize,
    formatter_name: &str,
    compression: Option<&str>,
    encrypt_key: Option<&str>,
) -> PyResult<NativeTransport> {
    let options = FileOptions {
        max_bytes,
        backup_count,
        formatter: formatter(formatter_name)?,
        compression: compression
            .map(Compression::parse)
            .transpose()
            .map_err(PyValueError::new_err)?,
        encrypt_key: encrypt_key.map(str::to_string),
    };
    FileTransport::new(path, options)
        .map(NativeTransport::new)
        .map_err(PyValueError::new_err)
}

/// POSTs batches of formatted lines, newline-joined, to `url`.
#[pyfunction]
#[pyo3(signature = (url, formatter_name = "json", max_records = 50, max_bytes = 1_000_000, timeout = 10.0))]
pub fn http_transport(
    url: &str,
    formatter_name: &str,
    max_records: usize,
    max_bytes: usize,
    timeout: f64,
) -> PyResult<NativeTransport> {
    let options = HttpOptions {
        max_records,
        max_bytes,
        timeout: std::time::Duration::from_secs_f64(timeout),
        formatter: formatter(formatter_name)?,
    };
    Ok(NativeTransport::new(logquill_runtime::http_transport(
        url,
        options,
        Box::new(UreqPost),
    )))
}
