//! Timing budget, enforced by CI in release mode (`cargo test --release -p logquill-runtime-checks
//! --test timing`). Wall-clock time differs between machines, so the budget is a ratio: the time to
//! emit and deliver N records divided by the time, on the same machine in the same run, to
//! serialise the same N records with `serde_json`. A slow CI runner slows both and cancels out.

use std::sync::Arc;
use std::time::{Duration, Instant};

use logquill_runtime::{Backpressure, Dispatcher, Transport, WorkerOptions};
use serde_json::{json, Value};

const RECORDS: usize = 50_000;
const RUNS: usize = 7;
/// About 5 on a laptop and 14 on a 2-core CI runner (the producer and worker contend on the queue lock,
/// so the ratio depends on core count). 40 is coarse: it catches order-of-magnitude regressions only.
const MAX_RATIO: f64 = 40.0;

struct Null;

impl Transport for Null {
    fn format(&self, _record: &Value) -> String {
        String::new()
    }

    fn write(&mut self, _formatted: &str, _record: &Value) -> Result<(), String> {
        Ok(())
    }
}

fn records() -> Vec<Value> {
    (0..RECORDS)
        .map(|i| json!({"level": "INFO", "message": "hello", "meta": {"i": i, "user": "u-1"}}))
        .collect()
}

fn median(mut times: Vec<Duration>) -> Duration {
    times.sort();
    times[times.len() / 2]
}

#[test]
#[cfg_attr(debug_assertions, ignore = "timing is only meaningful in release mode")]
fn emitting_stays_within_a_fixed_multiple_of_serialising() {
    let options = WorkerOptions {
        max_queue_size: RECORDS,
        backpressure: Backpressure::DropOldest,
        warn_interval: Duration::from_secs(3600),
        warn: Arc::new(|_| {}),
    };
    let dispatcher = Dispatcher::new(options, vec![Box::new(Null)]).unwrap();

    let (mut emit, mut baseline) = (Vec::new(), Vec::new());
    for _ in 0..RUNS {
        let batch = records();
        let start = Instant::now();
        for record in batch {
            dispatcher.emit(record);
        }
        assert!(dispatcher.flush(Some(Duration::from_secs(30))));
        emit.push(start.elapsed());

        let batch = records();
        let start = Instant::now();
        for record in &batch {
            std::hint::black_box(serde_json::to_string(record).unwrap());
        }
        baseline.push(start.elapsed());
    }
    let ratio = median(emit).as_secs_f64() / median(baseline).as_secs_f64();
    println!("emit / serialise ratio: {ratio:.2} (budget {MAX_RATIO})");
    assert!(
        ratio <= MAX_RATIO,
        "emitting {RECORDS} records now takes {ratio:.2}x the time of serialising them (budget \
         {MAX_RATIO}x) \u{2014} the emit path or the worker hand-off got slower"
    );
    assert!(dispatcher.close(Some(Duration::from_secs(30))));
}
