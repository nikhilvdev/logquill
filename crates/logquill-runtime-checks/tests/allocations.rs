//! Allocation budgets, enforced by CI (the "benchmark and allocation budgets" exit criterion).
//! A counting global allocator measures two things on the dispatcher's hot path:
//! allocations per record, and peak live memory while a sink is stalled.

use std::alloc::{GlobalAlloc, Layout, System};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::Duration;

use logquill_runtime::{Backpressure, Dispatcher, Transport, WorkerOptions};
use serde_json::{json, Value};

static ALLOCATIONS: AtomicUsize = AtomicUsize::new(0);
static LIVE: AtomicUsize = AtomicUsize::new(0);
static PEAK: AtomicUsize = AtomicUsize::new(0);

struct Counting;

// SAFETY: every call is forwarded unchanged to the system allocator; the counters only observe.
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        ALLOCATIONS.fetch_add(1, Ordering::Relaxed);
        let live = LIVE.fetch_add(layout.size(), Ordering::Relaxed) + layout.size();
        PEAK.fetch_max(live, Ordering::Relaxed);
        System.alloc(layout)
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        LIVE.fetch_sub(layout.size(), Ordering::Relaxed);
        System.dealloc(ptr, layout)
    }
}

#[global_allocator]
static ALLOCATOR: Counting = Counting;

/// Accepts every record without allocating, so the count is the dispatcher's own.
struct Null;

impl Transport for Null {
    fn format(&self, _record: &Value) -> String {
        String::new()
    }

    fn write(&mut self, _formatted: &str, _record: &Value) -> Result<(), String> {
        Ok(())
    }
}

/// Blocks in `write` until opened, to stall the worker.
struct Stalled(Arc<(Mutex<bool>, Condvar)>);

impl Transport for Stalled {
    fn format(&self, _record: &Value) -> String {
        String::new()
    }

    fn write(&mut self, _formatted: &str, _record: &Value) -> Result<(), String> {
        let (open, cv) = &*self.0;
        let mut guard = open.lock().unwrap();
        while !*guard {
            guard = cv.wait(guard).unwrap();
        }
        Ok(())
    }
}

fn options(max: usize) -> WorkerOptions {
    WorkerOptions {
        max_queue_size: max,
        backpressure: Backpressure::DropOldest,
        warn_interval: Duration::from_secs(3600),
        warn: Arc::new(|_| {}),
    }
}

// One test, so nothing else in the process allocates while a budget is being measured.
#[test]
fn the_hot_path_stays_inside_its_allocation_budgets() {
    // Budget 1: at most 2 allocations per record (the boxed task, plus queue growth amortised).
    // Today it is about 1; the headroom is for a Vec resize, not for a new per-record copy.
    const RECORDS: usize = 10_000;
    let dispatcher = Dispatcher::new(options(RECORDS), vec![Box::new(Null)]).unwrap();
    let records: Vec<Value> = (0..RECORDS)
        .map(|i| json!({"level": "INFO", "message": "hello", "meta": {"i": i}}))
        .collect();
    let before = ALLOCATIONS.load(Ordering::Relaxed);
    for record in records {
        dispatcher.emit(record);
    }
    assert!(dispatcher.flush(Some(Duration::from_secs(10))));
    let per_record = (ALLOCATIONS.load(Ordering::Relaxed) - before) as f64 / RECORDS as f64;
    assert!(
        per_record <= 2.0,
        "the dispatcher now allocates {per_record:.2} times per record (budget 2.0) \u{2014} \
         look for a new clone or format! on the emit path"
    );
    assert!(dispatcher.close(Some(Duration::from_secs(10))));

    // Budget 2: with the sink stalled and a queue bound of 1,000, 100,000 records add at most
    // 8 MiB of live memory. Unbounded growth would be well over 50 MiB.
    let gate = Arc::new((Mutex::new(false), Condvar::new()));
    let stalled = Dispatcher::new(options(1_000), vec![Box::new(Stalled(gate.clone()))]).unwrap();
    let baseline = LIVE.load(Ordering::Relaxed);
    PEAK.store(baseline, Ordering::Relaxed);
    for i in 0..100_000 {
        stalled.emit(json!({"level": "INFO", "message": "hello", "meta": {"i": i}}));
    }
    let growth = PEAK.load(Ordering::Relaxed) - baseline;
    assert!(
        growth <= 8 * 1024 * 1024,
        "a stalled sink let live memory grow by {growth} bytes (budget 8 MiB) \u{2014} the queue \
         bound is no longer holding"
    );
    *gate.0.lock().unwrap() = true;
    gate.1.notify_all();
    assert!(stalled.close(Some(Duration::from_secs(10))));
}
