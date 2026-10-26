//! Hands finished records from callers to transports on the worker thread, so a log call never
//! waits on a sink (`docs/spec/05-runtime-and-transports.md`).

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde_json::Value;

use crate::transport::Transport;
use crate::worker::{Worker, WorkerOptions};

type Transports = Arc<Mutex<Vec<Box<dyn Transport>>>>;

/// A worker plus the transports it feeds.
pub struct Dispatcher {
    worker: Worker,
    transports: Transports,
    warn: Arc<dyn Fn(&str) + Send + Sync>,
    closed: Mutex<bool>,
}

impl Dispatcher {
    /// Starts the worker. Transports are written in the order given.
    pub fn new(
        options: WorkerOptions,
        transports: Vec<Box<dyn Transport>>,
    ) -> Result<Dispatcher, String> {
        let warn = options.warn.clone();
        Ok(Dispatcher {
            worker: Worker::new(options)?,
            transports: Arc::new(Mutex::new(transports)),
            warn,
            closed: Mutex::new(false),
        })
    }

    /// Queues `record` for every transport. Returns whether it was accepted.
    pub fn emit(&self, record: Value) -> bool {
        let (transports, warn) = (self.transports.clone(), self.warn.clone());
        self.worker.submit(move || {
            let mut list = transports.lock().unwrap_or_else(|p| p.into_inner());
            for transport in list.iter_mut() {
                let outcome = catch_unwind(AssertUnwindSafe(|| {
                    let line = transport.format(&record);
                    transport.write(&line, &record)
                }));
                match outcome {
                    Ok(Ok(())) => {}
                    Ok(Err(error)) => warn(&format!(
                        "a transport failed to write a record \u{2014} {error}"
                    )),
                    Err(_) => warn("a transport panicked while writing a record"),
                }
            }
        })
    }

    /// Waits for queued records, then asks every transport to send what it buffers. Does not
    /// close anything, so a serverless handler can flush and keep logging.
    pub fn flush(&self, timeout: Option<Duration>) -> bool {
        // If the drain timed out the worker is still writing and holds the transports (after a
        // `fork` it may hold them forever), so waiting for them here would ignore `timeout`.
        if !self.worker.drain(timeout) {
            return false;
        }
        let mut list = self.transports.lock().unwrap_or_else(|p| p.into_inner());
        for transport in list.iter_mut() {
            if let Err(error) = transport.flush() {
                (self.warn)(&format!("a transport failed to flush \u{2014} {error}"));
            }
        }
        true
    }

    /// Drains, closes the worker, then closes each transport once. Idempotent. If the drain does
    /// not finish in time the transports are left open (the worker is still using them) and a
    /// later call tries again.
    pub fn close(&self, timeout: Option<Duration>) -> bool {
        if !self.worker.close(timeout) {
            return false;
        }
        let mut closed = self.closed.lock().unwrap_or_else(|p| p.into_inner());
        if !*closed {
            *closed = true;
            let mut list = self.transports.lock().unwrap_or_else(|p| p.into_inner());
            for transport in list.iter_mut() {
                if let Err(error) = transport.close() {
                    (self.warn)(&format!("a transport failed to close \u{2014} {error}"));
                }
            }
        }
        true
    }

    /// The worker's counters.
    pub fn stats(&self) -> crate::worker::Stats {
        self.worker.stats()
    }
}

impl Drop for Dispatcher {
    fn drop(&mut self) {
        self.close(Some(Duration::from_secs(5)));
    }
}
