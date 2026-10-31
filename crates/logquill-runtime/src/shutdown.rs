//! Flushing and closing at the end of a process, and around serverless handlers
//! (`docs/spec/05-runtime-and-transports.md`, "Exit and shutdown" and "Serverless").
//!
//! The core cannot install an exit hook (that is host-specific: `atexit` in Python, `beforeExit`
//! in Node), so this module provides the registry and the function the host's hook calls.

use std::panic::{catch_unwind, resume_unwind, AssertUnwindSafe};
use std::sync::{Arc, Mutex, OnceLock, Weak};
use std::time::Duration;

use crate::dispatcher::Dispatcher;

fn registry() -> &'static Mutex<Vec<Weak<Dispatcher>>> {
    static REGISTRY: OnceLock<Mutex<Vec<Weak<Dispatcher>>>> = OnceLock::new();
    REGISTRY.get_or_init(|| Mutex::new(Vec::new()))
}

/// Remembers a dispatcher (weakly, so it can still be dropped) for [`close_all`].
pub fn register(dispatcher: &Arc<Dispatcher>) {
    let mut list = registry().lock().unwrap_or_else(|p| p.into_inner());
    list.retain(|weak| weak.strong_count() > 0);
    if !list
        .iter()
        .any(|weak| weak.as_ptr() == Arc::as_ptr(dispatcher))
    {
        list.push(Arc::downgrade(dispatcher));
    }
}

/// Closes every registered dispatcher that is still alive, each with `timeout`, and never panics.
/// Returns how many did not finish draining in time. Call it from the host's exit hook.
pub fn close_all(timeout: Duration, warn: &dyn Fn(&str)) -> usize {
    let alive: Vec<Arc<Dispatcher>> = {
        let mut list = registry().lock().unwrap_or_else(|p| p.into_inner());
        list.retain(|weak| weak.strong_count() > 0);
        list.iter().filter_map(Weak::upgrade).collect()
    };
    let mut late = 0;
    for dispatcher in alive {
        let finished =
            catch_unwind(AssertUnwindSafe(|| dispatcher.close(Some(timeout)))).unwrap_or(false);
        if !finished {
            late += 1;
            warn(&format!(
                "logquill: {}s wasn't enough to flush every queued record at exit \u{2014} a transport is slow or \
                 unreachable; close the dispatcher yourself with a longer timeout if these records matter",
                timeout.as_secs_f64()
            ));
        }
    }
    late
}

/// Runs `handler`, then flushes (not closes) every dispatcher even if the handler panicked, because
/// a warm serverless container reuses them. The handler's panic, if any, is passed on afterwards.
pub fn with_flush<R>(
    dispatchers: &[Arc<Dispatcher>],
    timeout: Duration,
    handler: impl FnOnce() -> R,
) -> R {
    let outcome = catch_unwind(AssertUnwindSafe(handler));
    for dispatcher in dispatchers {
        let _ = catch_unwind(AssertUnwindSafe(|| dispatcher.flush(Some(timeout))));
    }
    match outcome {
        Ok(value) => value,
        Err(panic) => resume_unwind(panic),
    }
}
