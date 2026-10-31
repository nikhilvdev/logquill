//! Hands finished records from callers to transports on the worker thread, so a log call never
//! waits on a sink (`docs/spec/05-runtime-and-transports.md`).

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use logquill_core::SharedChain;
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

    /// Writes `record` to every transport, in order. A transport that fails or panics is reported
    /// and never stops the others.
    fn deliver(transports: &Transports, warn: &Arc<dyn Fn(&str) + Send + Sync>, record: &Value) {
        let mut list = transports.lock().unwrap_or_else(|p| p.into_inner());
        for transport in list.iter_mut() {
            let outcome = catch_unwind(AssertUnwindSafe(|| {
                let line = transport.format(record);
                transport.write(&line, record)
            }));
            match outcome {
                Ok(Ok(())) => {}
                Ok(Err(error)) => warn(&format!(
                    "a transport failed to write a record \u{2014} {error}"
                )),
                Err(_) => warn("a transport panicked while writing a record"),
            }
        }
    }

    /// Queues `record` for every transport. Returns whether it was accepted.
    pub fn emit(&self, record: Value) -> bool {
        self.emit_then(record, None, |_| {})
    }

    /// Like [`Dispatcher::emit`], with two extras. `chain`, if given, stamps the record into a hash
    /// chain on the worker thread, in queue order, immediately before it is written (decision D22:
    /// the chain is made at write time, one record at a time). `then` runs on the worker once every
    /// transport has written the record, and sees it as written (for a plugin's `after_log`); a
    /// panic in it is reported, not fatal.
    pub fn emit_then(
        &self,
        record: Value,
        chain: Option<Arc<SharedChain>>,
        then: impl FnOnce(&Value) + Send + 'static,
    ) -> bool {
        let (transports, warn) = (self.transports.clone(), self.warn.clone());
        self.worker.submit(move || {
            Dispatcher::deliver_chained(&transports, &warn, record, chain.as_deref(), then);
        })
    }

    /// Stamps (if chained), writes and runs the hook, all inside the chain's critical section.
    fn deliver_chained(
        transports: &Transports,
        warn: &Arc<dyn Fn(&str) + Send + Sync>,
        record: Value,
        chain: Option<&SharedChain>,
        then: impl FnOnce(&Value),
    ) {
        let write = |record: &Value| {
            Dispatcher::deliver(transports, warn, record);
            if catch_unwind(AssertUnwindSafe(|| then(record))).is_err() {
                warn("an after-write hook panicked");
            }
        };
        match chain {
            Some(chain) => chain.write(record, write),
            None => write(&record),
        }
    }

    /// Writes `record` to every transport now, on the calling thread, without the queue (stamped
    /// first if `chain` is given). For loggers that choose synchronous dispatch; `then` runs
    /// afterwards. Does nothing once the dispatcher is closed.
    pub fn write_now(&self, record: Value, chain: Option<&SharedChain>, then: impl FnOnce(&Value)) {
        if *self.closed.lock().unwrap_or_else(|p| p.into_inner()) {
            return;
        }
        Dispatcher::deliver_chained(&self.transports, &self.warn, record, chain, then);
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
    /// Writes what is queued and flushes the transports, but does not close them: a transport may
    /// be shared with other dispatchers, and closing is an explicit act ([`Dispatcher::close`]).
    fn drop(&mut self) {
        let already_closed = *self.closed.lock().unwrap_or_else(|p| p.into_inner());
        if !already_closed && self.worker.close(Some(Duration::from_secs(5))) {
            let mut list = self.transports.lock().unwrap_or_else(|p| p.into_inner());
            for transport in list.iter_mut() {
                let _ = transport.flush();
            }
        }
    }
}
