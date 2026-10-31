//! The dispatcher as a Python class: a bounded queue, one worker thread, and the transports.
//!
//! The GIL is released while waiting (`flush`, `close`), because the worker may need it to run a
//! Python transport, and waiting with it held would deadlock.

use std::mem::ManuallyDrop;
use std::sync::Arc;
use std::time::Duration;

use logquill_runtime::{Backpressure, Dispatcher, Transport, WorkerOptions};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::convert::{to_py, to_value, NonFinite};
use crate::plugins::PyChain;
use crate::transports::{HostTransport, NativeTransport, SharedTransport};

fn transport_for(object: &Bound<'_, PyAny>) -> Box<dyn Transport> {
    match object.cast::<NativeTransport>() {
        Ok(native) => Box::new(SharedTransport(native.get().inner.clone())),
        Err(_) => Box::new(HostTransport::new(object.clone().unbind())),
    }
}

/// A warning sink: the Python callable if one was given, else standard error.
fn warner(warn: Option<Py<PyAny>>) -> Arc<dyn Fn(&str) + Send + Sync> {
    Arc::new(move |message: &str| match &warn {
        Some(callable) => {
            let delivered = Python::attach(|py| callable.call1(py, (message,)).is_ok());
            if !delivered {
                eprintln!("logquill: {message}");
            }
        }
        None => eprintln!("logquill: {message}"),
    })
}

fn seconds(timeout: Option<f64>) -> Option<Duration> {
    timeout.map(|s| Duration::from_secs_f64(s.max(0.0)))
}

/// A callback that runs on the writing thread with the record as written.
fn hook(callback: Option<Py<PyAny>>) -> impl FnOnce(&serde_json::Value) + Send + 'static {
    move |record| {
        if let Some(callback) = callback {
            Python::attach(|py| {
                if let Ok(dict) = to_py(py, record) {
                    let _ = callback.call1(py, (dict,));
                }
            });
        }
    }
}

fn submit(
    inner: &Dispatcher,
    value: serde_json::Value,
    after: Option<Py<PyAny>>,
    chain: Option<Arc<logquill_core::SharedChain>>,
) -> bool {
    inner.emit_then(value, chain, hook(after))
}

#[pyclass(name = "Dispatcher", frozen)]
pub struct PyDispatcher {
    inner: ManuallyDrop<Arc<Dispatcher>>,
    /// Whether a full queue makes `emit` wait (`backpressure="block"`).
    blocking: bool,
}

impl Drop for PyDispatcher {
    /// Dropping drains the queue, and the worker may need the GIL to finish a Python transport's
    /// write, so the GIL must not be held while waiting for it.
    fn drop(&mut self) {
        // SAFETY: `inner` is taken exactly once, here, and never used afterwards.
        let inner = unsafe { ManuallyDrop::take(&mut self.inner) };
        Python::attach(|py| py.detach(move || drop(inner)));
    }
}

#[pymethods]
impl PyDispatcher {
    /// `transports` are `NativeTransport`s or Python objects with `format(record)`,
    /// `write(formatted, record)` and optionally `flush()` / `close()`.
    #[new]
    #[pyo3(signature = (transports, max_queue_size = 10_000, backpressure = "drop_oldest", warn = None, warn_interval = 60.0))]
    fn new(
        transports: Vec<Bound<'_, PyAny>>,
        max_queue_size: usize,
        backpressure: &str,
        warn: Option<Py<PyAny>>,
        warn_interval: f64,
    ) -> PyResult<PyDispatcher> {
        let policy = backpressure
            .parse::<Backpressure>()
            .map_err(PyValueError::new_err)?;
        let options = WorkerOptions {
            max_queue_size,
            backpressure: policy,
            warn_interval: Duration::from_secs_f64(warn_interval.max(0.0)),
            warn: warner(warn),
        };
        let list = transports.iter().map(transport_for).collect();
        let inner = Dispatcher::new(options, list).map_err(PyValueError::new_err)?;
        Ok(PyDispatcher {
            inner: ManuallyDrop::new(Arc::new(inner)),
            blocking: policy == Backpressure::Block,
        })
    }

    /// Queues `record` for every transport and returns whether it was accepted. `after`, if given,
    /// is called with the record on the worker thread once every transport has written it.
    ///
    /// With `backpressure="block"` a full queue makes this wait, and the worker may need the GIL
    /// to make room, so the GIL is released for the wait. Otherwise it is kept: releasing it costs
    /// more than the (never blocking) submit.
    #[pyo3(signature = (record, after = None, chain = None))]
    fn emit(
        &self,
        py: Python<'_>,
        record: &Bound<'_, PyAny>,
        after: Option<Py<PyAny>>,
        chain: Option<&Bound<'_, PyChain>>,
    ) -> PyResult<bool> {
        let value = to_value(record, NonFinite::Null)?;
        let chain = chain.map(|c| Arc::clone(&c.get().inner));
        let inner = Arc::clone(&self.inner);
        Ok(if self.blocking {
            py.detach(move || submit(&inner, value, after, chain))
        } else {
            submit(&inner, value, after, chain)
        })
    }

    /// Writes `record` to every transport now, on this thread, bypassing the queue (chained first
    /// if `chain` is given). `after` is called with the record as written.
    #[pyo3(signature = (record, after = None, chain = None))]
    fn write_now(
        &self,
        py: Python<'_>,
        record: &Bound<'_, PyAny>,
        after: Option<Py<PyAny>>,
        chain: Option<&Bound<'_, PyChain>>,
    ) -> PyResult<()> {
        let value = to_value(record, NonFinite::Null)?;
        let chain = chain.map(|c| Arc::clone(&c.get().inner));
        let inner = Arc::clone(&self.inner);
        py.detach(move || inner.write_now(value, chain.as_deref(), hook(after)));
        Ok(())
    }

    /// Waits for queued records, then flushes every transport. Returns whether the queue drained
    /// within `timeout` seconds (`None` waits for as long as it takes).
    #[pyo3(signature = (timeout = None))]
    fn flush(&self, py: Python<'_>, timeout: Option<f64>) -> bool {
        let inner = Arc::clone(&self.inner);
        py.detach(move || inner.flush(seconds(timeout)))
    }

    /// Drains the queue, stops the worker and closes every transport. Safe to repeat.
    #[pyo3(signature = (timeout = Some(5.0)))]
    fn close(&self, py: Python<'_>, timeout: Option<f64>) -> bool {
        let inner = Arc::clone(&self.inner);
        py.detach(move || inner.close(seconds(timeout)))
    }

    /// Counters: `queued`, `submitted`, `finished` and `dropped`.
    fn stats<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let stats = self.inner.stats();
        let out = PyDict::new(py);
        out.set_item("queued", stats.queued)?;
        out.set_item("submitted", stats.submitted)?;
        out.set_item("finished", stats.finished)?;
        out.set_item("dropped", stats.dropped)?;
        Ok(out)
    }
}

/// A bounded queue and one worker thread running Python callables: the public `AsyncWorker`.
#[pyclass(name = "Worker", frozen)]
pub struct PyWorker {
    inner: ManuallyDrop<Arc<logquill_runtime::Worker>>,
    blocking: bool,
}

impl Drop for PyWorker {
    /// See [`PyDispatcher`]: draining may need the GIL, so it must not be held.
    fn drop(&mut self) {
        // SAFETY: `inner` is taken exactly once, here, and never used afterwards.
        let inner = unsafe { ManuallyDrop::take(&mut self.inner) };
        Python::attach(|py| py.detach(move || drop(inner)));
    }
}

#[pymethods]
impl PyWorker {
    #[new]
    #[pyo3(signature = (max_queue_size = 10_000, backpressure = "drop_oldest", warn = None, warn_interval = 60.0))]
    fn new(
        max_queue_size: usize,
        backpressure: &str,
        warn: Option<Py<PyAny>>,
        warn_interval: f64,
    ) -> PyResult<PyWorker> {
        let policy = backpressure
            .parse::<Backpressure>()
            .map_err(PyValueError::new_err)?;
        let worker = logquill_runtime::Worker::new(WorkerOptions {
            max_queue_size,
            backpressure: policy,
            warn_interval: Duration::from_secs_f64(warn_interval.max(0.0)),
            warn: warner(warn),
        })
        .map_err(PyValueError::new_err)?;
        Ok(PyWorker {
            inner: ManuallyDrop::new(Arc::new(worker)),
            blocking: policy == Backpressure::Block,
        })
    }

    /// Queues `item`, a callable taking no arguments, for the worker thread. Returns whether it
    /// was accepted; after `close` it is silently refused. With `backpressure="block"` a full
    /// queue makes this wait, with the GIL released.
    fn submit(&self, py: Python<'_>, item: Py<PyAny>) -> bool {
        let inner = Arc::clone(&self.inner);
        let task = move || {
            Python::attach(|py| {
                if let Err(error) = item.call0(py) {
                    eprintln!("logquill: a queued work item raised: {error}");
                }
            });
        };
        if self.blocking {
            py.detach(move || inner.submit(task))
        } else {
            inner.submit(task)
        }
    }

    /// Waits until everything submitted so far has finished, or `timeout` seconds pass. Returns
    /// whether it finished.
    #[pyo3(signature = (timeout = None))]
    fn drain(&self, py: Python<'_>, timeout: Option<f64>) -> bool {
        let inner = Arc::clone(&self.inner);
        py.detach(move || inner.drain(seconds(timeout)))
    }

    /// Drains, then stops the thread. Safe to repeat. Returns whether the drain finished in time.
    #[pyo3(signature = (timeout = Some(5.0)))]
    fn close(&self, py: Python<'_>, timeout: Option<f64>) -> bool {
        let inner = Arc::clone(&self.inner);
        py.detach(move || inner.close(seconds(timeout)))
    }

    /// How many items are waiting to run.
    fn queued(&self) -> usize {
        self.inner.stats().queued
    }
}
