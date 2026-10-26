//! The worker's queue policies, shutdown and fork behaviour; the transports; and the
//! burst-and-stall test: a stalled sink must not make memory grow.

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Duration;

use logquill_runtime::{
    Backpressure, BatchSender, Batching, Console, Dispatcher, FileOptions, FileTransport,
    Formatter, Transport, Worker, WorkerOptions,
};
use serde_json::{json, Value};

/// A door tasks wait at until the test opens it.
#[derive(Clone, Default)]
struct Gate(Arc<(Mutex<bool>, Condvar)>);

impl Gate {
    fn wait(&self) {
        let (open, cv) = &*self.0;
        let mut guard = open.lock().unwrap();
        while !*guard {
            guard = cv.wait(guard).unwrap();
        }
    }

    fn open(&self) {
        *self.0 .0.lock().unwrap() = true;
        self.0 .1.notify_all();
    }
}

fn options(max: usize, policy: Backpressure) -> (WorkerOptions, Arc<Mutex<Vec<String>>>) {
    let warnings = Arc::new(Mutex::new(Vec::new()));
    let sink = warnings.clone();
    let options = WorkerOptions {
        max_queue_size: max,
        backpressure: policy,
        warn_interval: Duration::from_secs(3600),
        warn: Arc::new(move |m| sink.lock().unwrap().push(m.to_string())),
    };
    (options, warnings)
}

type Stalled = (Worker, Gate, Arc<Mutex<Vec<u32>>>, Arc<Mutex<Vec<String>>>);

/// Starts a worker whose first task holds the thread until the gate opens, so everything
/// submitted afterwards queues up.
fn stalled(max: usize, policy: Backpressure) -> Stalled {
    let (opts, warnings) = options(max, policy);
    let worker = Worker::new(opts).unwrap();
    let gate = Gate::default();
    let ran = Arc::new(Mutex::new(Vec::new()));
    let (started_tx, started_rx) = std::sync::mpsc::channel();
    let (g, r) = (gate.clone(), ran.clone());
    assert!(worker.submit(move || {
        started_tx.send(()).unwrap();
        g.wait();
        r.lock().unwrap().push(0);
    }));
    started_rx.recv().unwrap(); // the worker has taken it, so the queue is empty again
    (worker, gate, ran, warnings)
}

fn push(ran: &Arc<Mutex<Vec<u32>>>, n: u32) -> impl FnOnce() + Send + 'static {
    let ran = ran.clone();
    move || ran.lock().unwrap().push(n)
}

#[test]
fn tasks_run_in_submission_order() {
    let (opts, _) = options(100, Backpressure::DropOldest);
    let worker = Worker::new(opts).unwrap();
    let ran = Arc::new(Mutex::new(Vec::new()));
    for n in 1..=50 {
        assert!(worker.submit(push(&ran, n)));
    }
    assert!(worker.drain(Some(Duration::from_secs(5))));
    assert_eq!(*ran.lock().unwrap(), (1..=50).collect::<Vec<_>>());
}

#[test]
fn drop_oldest_keeps_the_newest_items() {
    let (worker, gate, ran, warnings) = stalled(3, Backpressure::DropOldest);
    for n in 1..=10 {
        worker.submit(push(&ran, n));
    }
    assert_eq!(worker.stats().queued, 3);
    gate.open();
    assert!(worker.drain(Some(Duration::from_secs(5))));
    assert_eq!(*ran.lock().unwrap(), vec![0, 8, 9, 10]);
    assert_eq!(worker.stats().dropped, 7);
    let warnings = warnings.lock().unwrap();
    assert_eq!(
        warnings.len(),
        1,
        "one warning per interval, not one per drop"
    );
    assert!(
        warnings[0].contains("max_queue_size=3")
            && warnings[0].contains("backpressure=drop_oldest")
    );
}

#[test]
fn drop_newest_keeps_the_oldest_items() {
    let (worker, gate, ran, _) = stalled(3, Backpressure::DropNewest);
    let accepted: Vec<bool> = (1..=10).map(|n| worker.submit(push(&ran, n))).collect();
    assert_eq!(accepted.iter().filter(|a| **a).count(), 3);
    gate.open();
    assert!(worker.drain(Some(Duration::from_secs(5))));
    assert_eq!(*ran.lock().unwrap(), vec![0, 1, 2, 3]);
}

#[test]
fn block_makes_the_producer_wait_and_loses_nothing() {
    let (worker, gate, ran, warnings) = stalled(2, Backpressure::Block);
    let worker = Arc::new(worker);
    let producer = {
        let (worker, ran) = (worker.clone(), ran.clone());
        thread::spawn(move || {
            for n in 1..=10 {
                worker.submit(push(&ran, n));
            }
        })
    };
    thread::sleep(Duration::from_millis(100));
    assert!(
        !producer.is_finished(),
        "the producer should be waiting for room"
    );
    assert!(worker.stats().queued <= 2);
    gate.open();
    producer.join().unwrap();
    assert!(worker.drain(Some(Duration::from_secs(5))));
    assert_eq!(*ran.lock().unwrap(), (0..=10).collect::<Vec<_>>());
    assert_eq!(worker.stats().dropped, 0);
    assert!(warnings.lock().unwrap().is_empty());
}

#[test]
fn closing_wakes_a_blocked_producer_and_later_submissions_are_refused() {
    let (worker, gate, ran, _) = stalled(1, Backpressure::Block);
    let worker = Arc::new(worker);
    worker.submit(push(&ran, 1));
    let blocked = {
        let (worker, ran) = (worker.clone(), ran.clone());
        thread::spawn(move || worker.submit(push(&ran, 2)))
    };
    thread::sleep(Duration::from_millis(100));
    let closer = {
        let worker = worker.clone();
        thread::spawn(move || worker.close(Some(Duration::from_secs(5))))
    };
    thread::sleep(Duration::from_millis(100));
    gate.open();
    blocked.join().unwrap();
    assert!(closer.join().unwrap());
    assert!(!worker.submit(|| {}), "a submission after close is refused");
    assert!(worker.close(None), "close is idempotent");
}

#[test]
fn drain_reports_a_timeout_and_a_panicking_task_does_not_stop_the_worker() {
    let (worker, gate, ran, warnings) = stalled(10, Backpressure::DropOldest);
    worker.submit(|| panic!("boom"));
    worker.submit(push(&ran, 1));
    assert!(
        !worker.drain(Some(Duration::from_millis(50))),
        "stalled: drain times out"
    );
    gate.open();
    assert!(worker.drain(Some(Duration::from_secs(5))));
    assert_eq!(*ran.lock().unwrap(), vec![0, 1]);
    assert!(warnings
        .lock()
        .unwrap()
        .iter()
        .any(|w| w.contains("panicked")));
}

#[test]
fn the_worker_starts_over_in_a_forked_child() {
    let pid = Arc::new(AtomicU32::new(100));
    let (opts, _) = options(10, Backpressure::DropOldest);
    let source = pid.clone();
    let worker =
        Worker::with_process_id(opts, Arc::new(move || source.load(Ordering::SeqCst))).unwrap();
    let gate = Gate::default();
    let (started_tx, started_rx) = std::sync::mpsc::channel();
    let g = gate.clone();
    worker.submit(move || {
        started_tx.send(()).unwrap();
        g.wait(); // the "parent" thread, which does not exist in a real child
    });
    started_rx.recv().unwrap();
    pid.store(200, Ordering::SeqCst); // now acting as the forked child
    let ran = Arc::new(Mutex::new(Vec::new()));
    worker.submit(push(&ran, 1));
    assert!(
        worker.drain(Some(Duration::from_secs(5))),
        "the child's queue drains"
    );
    assert_eq!(*ran.lock().unwrap(), vec![1]);
    gate.open();
}

#[test]
fn bad_settings_say_what_to_change() {
    let (mut opts, _) = options(1, Backpressure::Block);
    opts.max_queue_size = 0;
    assert_eq!(
        Worker::new(opts).err().unwrap(),
        "max_queue_size must be at least 1, got 0"
    );
    assert_eq!(
        "sideways".parse::<Backpressure>().unwrap_err(),
        "backpressure must be one of (\"drop_oldest\", \"drop_newest\", \"block\"), got \"sideways\""
    );
}

// ---- the burst-and-stall test ---------------------------------------------------------------

struct StalledSink {
    gate: Gate,
    written: Arc<AtomicUsize>,
}

impl Transport for StalledSink {
    fn format(&self, record: &Value) -> String {
        Formatter::Json.format(record)
    }

    fn write(&mut self, _formatted: &str, _record: &Value) -> Result<(), String> {
        self.gate.wait();
        self.written.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

#[test]
fn a_stalled_sink_keeps_memory_set_by_the_queue_bound() {
    const BURST: usize = 100_000;
    const BOUND: usize = 1000;
    let gate = Gate::default();
    let written = Arc::new(AtomicUsize::new(0));
    let (opts, warnings) = options(BOUND, Backpressure::DropOldest);
    let dispatcher = Dispatcher::new(
        opts,
        vec![Box::new(StalledSink {
            gate: gate.clone(),
            written: written.clone(),
        })],
    )
    .unwrap();
    let mut highest = 0;
    for n in 0..BURST {
        dispatcher.emit(json!({"level": "INFO", "message": format!("m{n}"), "meta": {"n": n}}));
        if n % 997 == 0 {
            highest = highest.max(dispatcher.stats().queued);
        }
    }
    let stats = dispatcher.stats();
    highest = highest.max(stats.queued);
    assert!(
        highest <= BOUND,
        "the queue grew to {highest}, past its bound of {BOUND}"
    );
    assert_eq!(stats.submitted, BURST as u64);
    assert!(
        stats.dropped >= (BURST - BOUND - 1) as u64,
        "dropped {}",
        stats.dropped
    );
    assert_eq!(
        warnings.lock().unwrap().len(),
        1,
        "a flood of drops warns once per interval"
    );

    gate.open();
    assert!(dispatcher.flush(Some(Duration::from_secs(10))));
    let kept = written.load(Ordering::SeqCst);
    assert!(
        kept <= BOUND + 1,
        "only the in-flight record and the bounded queue survive, got {kept}"
    );
    assert!(
        kept >= BOUND,
        "the newest {BOUND} records are delivered, got {kept}"
    );
}

// ---- transports -----------------------------------------------------------------------------

#[derive(Clone, Default)]
struct Capture(Arc<Mutex<Vec<u8>>>);

impl Write for Capture {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

fn text(capture: &Capture) -> String {
    String::from_utf8(capture.0.lock().unwrap().clone()).unwrap()
}

fn record(level: &str, message: &str) -> Value {
    json!({"schema_version": "2.0", "timestamp": "t", "level": level, "logger": "l", "message": message, "meta": {}})
}

#[test]
fn errors_go_to_stderr_and_colour_wraps_the_line() {
    let (out, err) = (Capture::default(), Capture::default());
    let mut console = Console::with_streams(
        Box::new(out.clone()),
        Box::new(err.clone()),
        true,
        Formatter::Text,
    );
    for level in ["INFO", "ERROR"] {
        let record = record(level, "hi");
        let line = console.format(&record);
        console.write(&line, &record).unwrap();
    }
    assert!(text(&out).starts_with("\x1b[32m") && text(&out).ends_with("\x1b[0m\n"));
    assert!(text(&err).starts_with("\x1b[31m"));
    assert!(!text(&out).contains("ERROR") && !text(&err).contains("INFO "));

    let plain = Capture::default();
    let mut console = Console::with_streams(
        Box::new(plain.clone()),
        Box::new(Capture::default()),
        false,
        Formatter::Json,
    );
    let record = record("WARN", "w");
    let line = console.format(&record);
    console.write(&line, &record).unwrap();
    assert!(!text(&plain).contains('\x1b'));
}

fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("logquill-runtime-{}-{name}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn write_lines(transport: &mut FileTransport, count: usize) {
    for n in 0..count {
        let record = record("INFO", &format!("message number {n:04}"));
        let line = transport.format(&record);
        transport.write(&line, &record).unwrap();
    }
}

#[test]
fn the_file_rotates_by_size_and_keeps_only_backup_count_files() {
    let dir = scratch("rotate");
    let path = dir.join("nested").join("app.log");
    let options = FileOptions {
        max_bytes: 400,
        backup_count: 2,
        formatter: Formatter::Json,
    };
    let mut transport = FileTransport::new(&path, options).unwrap();
    write_lines(&mut transport, 20);
    transport.close().unwrap();
    let names: Vec<String> = fs::read_dir(path.parent().unwrap())
        .unwrap()
        .map(|e| e.unwrap().file_name().to_string_lossy().to_string())
        .collect();
    assert!(names.contains(&"app.log".to_string()));
    assert!(names.contains(&"app.log.1".to_string()) && names.contains(&"app.log.2".to_string()));
    assert!(!names.contains(&"app.log.3".to_string()), "{names:?}");
    let newest = fs::read_to_string(&path).unwrap();
    assert!(
        newest.contains("0019")
            || fs::read_to_string(path.with_extension("log.1"))
                .unwrap()
                .contains("0019")
    );
}

#[test]
fn without_backups_a_rotation_deletes_the_file() {
    let dir = scratch("nobackup");
    let path = dir.join("app.log");
    let options = FileOptions {
        max_bytes: 300,
        backup_count: 0,
        formatter: Formatter::Json,
    };
    let mut transport = FileTransport::new(&path, options).unwrap();
    write_lines(&mut transport, 10);
    transport.close().unwrap();
    assert!(!dir.join("app.log.1").exists());
}

#[cfg(unix)]
#[test]
fn a_failed_rotation_does_not_leave_the_transport_without_a_file() {
    let dir = scratch("failed");
    let path = dir.join("app.log");
    // A non-empty directory where the backup should go makes the rename fail.
    fs::create_dir_all(dir.join("app.log.1").join("occupied")).unwrap();
    let options = FileOptions {
        max_bytes: 200,
        backup_count: 1,
        formatter: Formatter::Json,
    };
    let mut transport = FileTransport::new(&path, options).unwrap();
    let mut failures = 0;
    for n in 0..10 {
        let record = record("INFO", &format!("message number {n:04}"));
        let line = transport.format(&record);
        if transport.write(&line, &record).is_err() {
            failures += 1;
        }
    }
    assert!(failures > 0, "the rotation should have failed and said so");
    let last = record("INFO", "after the failure");
    let line = transport.format(&last);
    // The write itself lands even though the rotation after it keeps failing and says so.
    let _ = transport.write(&line, &last);
    assert!(fs::read_to_string(&path)
        .unwrap()
        .contains("after the failure"));
}

struct Recording {
    batches: Arc<Mutex<Vec<Vec<usize>>>>,
    fail: bool,
}

impl BatchSender<usize> for Recording {
    fn send(&mut self, batch: &[usize]) -> Result<(), String> {
        self.batches.lock().unwrap().push(batch.to_vec());
        if self.fail {
            Err("down".into())
        } else {
            Ok(())
        }
    }
}

type Batches = Arc<Mutex<Vec<Vec<usize>>>>;

fn batcher(max_records: usize, max_bytes: usize, fail: bool) -> (Batching<usize>, Batches) {
    let batches = Arc::new(Mutex::new(Vec::new()));
    let transport = Batching::new(
        Formatter::Json,
        Box::new(|line: &str, _| (line.len(), line.len())),
        Box::new(Recording {
            batches: batches.clone(),
            fail,
        }),
        max_records,
        max_bytes,
    );
    (transport, batches)
}

fn push_records(transport: &mut Batching<usize>, count: usize) {
    for n in 0..count {
        let record = record("INFO", &format!("m{n}"));
        let line = transport.format(&record);
        let _ = transport.write(&line, &record);
    }
}

#[test]
fn batches_flush_on_count_or_bytes_and_never_empty() {
    let (mut by_count, sent) = batcher(3, 1_000_000, false);
    push_records(&mut by_count, 7);
    assert_eq!(sent.lock().unwrap().len(), 2);
    by_count.close().unwrap();
    assert_eq!(sent.lock().unwrap().len(), 3);
    by_count.flush().unwrap();
    assert_eq!(
        sent.lock().unwrap().len(),
        3,
        "an empty buffer sends nothing"
    );

    let (mut by_bytes, sent) = batcher(1000, 300, false);
    push_records(&mut by_bytes, 10);
    let batches = sent.lock().unwrap();
    assert!(
        batches.len() >= 2,
        "bytes trigger a flush long before the count would"
    );
    assert!(batches.iter().all(|b| !b.is_empty()));
}

#[test]
fn a_failed_send_drops_the_batch_without_retrying() {
    let (mut transport, sent) = batcher(2, 1_000_000, true);
    push_records(&mut transport, 2);
    assert_eq!(transport.pending(), 0, "the failed batch is gone");
    assert_eq!(sent.lock().unwrap().len(), 1, "sent once, never retried");
}

#[test]
fn a_dispatcher_delivers_in_order_and_survives_a_failing_transport() {
    struct Failing;
    impl Transport for Failing {
        fn format(&self, record: &Value) -> String {
            Formatter::Json.format(record)
        }
        fn write(&mut self, _: &str, _: &Value) -> Result<(), String> {
            Err("disk full".into())
        }
    }
    let dir = scratch("dispatch");
    let path = dir.join("app.log");
    let file = FileTransport::new(&path, FileOptions::default()).unwrap();
    let (opts, warnings) = options(1000, Backpressure::DropOldest);
    let dispatcher = Dispatcher::new(opts, vec![Box::new(Failing), Box::new(file)]).unwrap();
    for n in 0..100 {
        dispatcher.emit(record("INFO", &format!("m{n}")));
    }
    assert!(dispatcher.close(Some(Duration::from_secs(5))));
    let lines: Vec<String> = fs::read_to_string(&path)
        .unwrap()
        .lines()
        .map(String::from)
        .collect();
    assert_eq!(lines.len(), 100);
    assert!(lines[0].contains("\"m0\"") && lines[99].contains("\"m99\""));
    assert_eq!(
        warnings.lock().unwrap().len(),
        100,
        "each failure is reported, none stops logging"
    );
}
