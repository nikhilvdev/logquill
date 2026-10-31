//! The Rust dispatcher and the native transports, for the SDK to hand records to.
//!
//! The SDK turns each record into JSON text (guarding against hostile `meta` on the JavaScript
//! side, where the value is) and calls [`Dispatcher::emit`]; a worker thread then formats and
//! writes it, so a log call never waits on a sink. Transports written in JavaScript stay on the
//! SDK's own queue: calling back into JavaScript from the worker thread would need the event
//! loop, which a synchronous `flush` at exit cannot give it.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use logquill_runtime::{
    Backpressure, Compression, Console, FileOptions, FileTransport, Formatter, HttpOptions,
    Transport, UreqPost, WorkerOptions,
};
use napi::bindgen_prelude::{AsyncTask, Task};
use napi::{Env, Error, Result};
use napi_derive::napi;
use serde_json::Value;

type Shared = Arc<Mutex<Box<dyn Transport>>>;

fn locked(shared: &Shared) -> std::sync::MutexGuard<'_, Box<dyn Transport>> {
    shared.lock().unwrap_or_else(|p| p.into_inner())
}

fn reason(message: impl Into<String>) -> Error {
    Error::from_reason(message.into())
}

pub(crate) fn formatter(name: Option<String>) -> Result<Formatter> {
    Formatter::parse(name.as_deref().unwrap_or("json")).map_err(reason)
}

/// A transport implemented in Rust. One instance can serve several loggers.
#[napi]
pub struct NativeTransport {
    inner: Shared,
}

impl NativeTransport {
    pub(crate) fn new(transport: impl Transport + 'static) -> NativeTransport {
        NativeTransport {
            inner: Arc::new(Mutex::new(Box::new(transport))),
        }
    }
}

fn record_value(record: Option<String>) -> Result<Value> {
    match record {
        None => Ok(Value::Object(Default::default())),
        Some(text) => serde_json::from_str(&text)
            .map_err(|e| reason(format!("the record is not valid JSON ({e})"))),
    }
}

#[napi]
impl NativeTransport {
    /// The line this transport writes for `record` (JSON text).
    #[napi]
    pub fn format(&self, record: String) -> Result<String> {
        Ok(locked(&self.inner).format(&record_value(Some(record))?))
    }

    /// Writes an already-formatted line now, on the calling thread. `record` (JSON text) is for
    /// transports that need its fields, such as the console's choice of stream.
    #[napi]
    pub fn write(&self, formatted: String, record: Option<String>) -> Result<()> {
        let record = record_value(record)?;
        locked(&self.inner)
            .write(&formatted, &record)
            .map_err(reason)
    }

    /// Sends whatever the transport buffers.
    #[napi]
    pub fn flush(&self) -> Result<()> {
        locked(&self.inner).flush().map_err(reason)
    }

    /// Flushes and releases the transport.
    #[napi]
    pub fn close(&self) -> Result<()> {
        locked(&self.inner).close().map_err(reason)
    }
}

/// What `fileTransport` accepts; every field is optional.
#[napi(object)]
pub struct FileConfig {
    /// Rotate once the file reaches this many bytes (default 10 MiB; 0 turns rotation off).
    pub max_bytes: Option<f64>,
    /// How many rotated files to keep (default 5).
    pub backup_count: Option<u32>,
    /// `"json"` (default), `"text"` or `"logfmt"`.
    pub formatter: Option<String>,
    /// `"gz"` or `"zip"`, applied to a file once it is rotated out.
    pub compression: Option<String>,
    /// A Fernet key: each line is encrypted before it is written.
    pub encrypt_key: Option<String>,
}

/// What `httpTransport` accepts; every field is optional.
#[napi(object)]
pub struct HttpConfig {
    pub formatter: Option<String>,
    /// Send a batch once it holds this many records (default 50).
    pub max_records: Option<u32>,
    /// ...or this many bytes (default 1,000,000).
    pub max_bytes: Option<f64>,
    /// Seconds to wait for the endpoint (default 10).
    pub timeout: Option<f64>,
}

/// Writes INFO and below to stdout and ERROR and above to stderr.
#[napi]
pub fn console_transport(colour: bool, formatter_name: Option<String>) -> Result<NativeTransport> {
    Ok(NativeTransport::new(Console::stdio(
        colour,
        formatter(formatter_name)?,
    )))
}

/// Appends records to a file, rotating by size; optionally compressing rotated files and
/// encrypting each line with a Fernet key.
#[napi]
pub fn file_transport(path: String, config: Option<FileConfig>) -> Result<NativeTransport> {
    let config = config.unwrap_or(FileConfig {
        max_bytes: None,
        backup_count: None,
        formatter: None,
        compression: None,
        encrypt_key: None,
    });
    let options = FileOptions {
        max_bytes: config
            .max_bytes
            .map_or(10 * 1024 * 1024, |n| n.max(0.0) as u64),
        backup_count: config.backup_count.map_or(5, |n| n as usize),
        formatter: formatter(config.formatter)?,
        compression: config
            .compression
            .as_deref()
            .map(Compression::parse)
            .transpose()
            .map_err(reason)?,
        encrypt_key: config.encrypt_key,
    };
    FileTransport::new(path, options)
        .map(NativeTransport::new)
        .map_err(reason)
}

/// POSTs batches of formatted lines, newline-joined, to `url`.
#[napi]
pub fn http_transport(url: String, config: Option<HttpConfig>) -> Result<NativeTransport> {
    let (formatter_name, max_records, max_bytes, timeout) = match config {
        Some(c) => (c.formatter, c.max_records, c.max_bytes, c.timeout),
        None => (None, None, None, None),
    };
    let options = HttpOptions {
        max_records: max_records.map_or(50, |n| n as usize),
        max_bytes: max_bytes.map_or(1_000_000, |n| n.max(0.0) as usize),
        timeout: Duration::from_secs_f64(timeout.unwrap_or(10.0).max(0.0)),
        formatter: formatter(formatter_name)?,
    };
    Ok(NativeTransport::new(logquill_runtime::http_transport(
        &url,
        options,
        Box::new(UreqPost),
    )))
}

/// What the `Dispatcher` constructor accepts.
#[napi(object)]
pub struct DispatchOptions {
    /// Most records queued at once (default 10,000).
    pub max_queue_size: Option<u32>,
    /// `"drop_oldest"` (default), `"drop_newest"` or `"block"`.
    pub backpressure: Option<String>,
}

/// A transport that passes everything to a [`NativeTransport`] shared with other dispatchers.
struct SharedTransport(Shared);

impl Transport for SharedTransport {
    fn format(&self, record: &Value) -> String {
        locked(&self.0).format(record)
    }
    fn write(&mut self, formatted: &str, record: &Value) -> std::result::Result<(), String> {
        locked(&self.0).write(formatted, record)
    }
    fn flush(&mut self) -> std::result::Result<(), String> {
        locked(&self.0).flush()
    }
    fn close(&mut self) -> std::result::Result<(), String> {
        locked(&self.0).close()
    }
}

/// A bounded queue and one worker thread that writes records to native transports.
#[napi]
pub struct Dispatcher {
    inner: Arc<logquill_runtime::Dispatcher>,
}

fn timeout(milliseconds: Option<f64>) -> Option<Duration> {
    milliseconds.map(|ms| Duration::from_secs_f64(ms.max(0.0) / 1000.0))
}

#[napi]
impl Dispatcher {
    #[napi(constructor)]
    pub fn new(
        transports: Vec<&NativeTransport>,
        options: Option<DispatchOptions>,
    ) -> Result<Self> {
        let (size, policy) = match options {
            Some(o) => (o.max_queue_size, o.backpressure),
            None => (None, None),
        };
        let backpressure = policy
            .as_deref()
            .unwrap_or("drop_oldest")
            .parse::<Backpressure>()
            .map_err(reason)?;
        let defaults = WorkerOptions::default();
        let worker = WorkerOptions {
            max_queue_size: size.map_or(defaults.max_queue_size, |n| n as usize),
            backpressure,
            warn: Arc::new(|message| eprintln!("logquill: {message}")),
            ..defaults
        };
        let list: Vec<Box<dyn Transport>> = transports
            .iter()
            .map(|t| Box::new(SharedTransport(t.inner.clone())) as Box<dyn Transport>)
            .collect();
        let inner = logquill_runtime::Dispatcher::new(worker, list).map_err(reason)?;
        Ok(Dispatcher {
            inner: Arc::new(inner),
        })
    }

    /// Queues one record, given as JSON text. Returns whether it was accepted.
    #[napi]
    pub fn emit(&self, record: String) -> Result<bool> {
        let value: Value = serde_json::from_str(&record)
            .map_err(|e| reason(format!("the record is not valid JSON ({e})")))?;
        Ok(self.inner.emit(value))
    }

    /// Waits for queued records, then flushes every transport; blocks the calling thread. For an
    /// `exit` handler, where a Promise cannot be awaited. Returns whether the queue drained.
    #[napi]
    pub fn flush_sync(&self, timeout_ms: Option<f64>) -> bool {
        self.inner.flush(timeout(timeout_ms))
    }

    /// Drains the queue, stops the worker and closes every transport; blocks the calling thread.
    #[napi]
    pub fn close_sync(&self, timeout_ms: Option<f64>) -> bool {
        self.inner.close(timeout(timeout_ms))
    }

    /// Like `flushSync`, off the main thread: resolves once the queue has drained.
    #[napi(ts_return_type = "Promise<boolean>")]
    pub fn flush(&self, timeout_ms: Option<f64>) -> AsyncTask<Drain> {
        AsyncTask::new(Drain {
            inner: self.inner.clone(),
            timeout: timeout(timeout_ms),
            close: false,
        })
    }

    /// Like `closeSync`, off the main thread.
    #[napi(ts_return_type = "Promise<boolean>")]
    pub fn close(&self, timeout_ms: Option<f64>) -> AsyncTask<Drain> {
        AsyncTask::new(Drain {
            inner: self.inner.clone(),
            timeout: timeout(timeout_ms),
            close: true,
        })
    }

    /// Records still queued.
    #[napi]
    pub fn queued(&self) -> u32 {
        let stats = self.inner.stats();
        u32::try_from(
            stats
                .submitted
                .saturating_sub(stats.finished + stats.dropped),
        )
        .unwrap_or(u32::MAX)
    }
}

/// Waits for the dispatcher on a worker-pool thread, so the event loop keeps running.
pub struct Drain {
    inner: Arc<logquill_runtime::Dispatcher>,
    timeout: Option<Duration>,
    close: bool,
}

impl Task for Drain {
    type Output = bool;
    type JsValue = bool;

    fn compute(&mut self) -> Result<bool> {
        Ok(if self.close {
            self.inner.close(self.timeout)
        } else {
            self.inner.flush(self.timeout)
        })
    }

    fn resolve(&mut self, _env: Env, output: bool) -> Result<bool> {
        Ok(output)
    }
}
