//! Level watchers, the exit-time close, and the serverless flush wrapper.

use std::fs;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use logquill_core::Level;
use logquill_runtime::{
    close_all, parse_level_setting, register, with_flush, Backpressure, Dispatcher, LevelSource,
    LevelWatcher, Transport, WorkerOptions,
};
use serde_json::{json, Value};

type Collected<T> = Arc<Mutex<Vec<T>>>;

fn collectors() -> (Collected<Level>, Collected<String>, LevelWatcherParts) {
    let levels = Arc::new(Mutex::new(Vec::new()));
    let reports = Arc::new(Mutex::new(Vec::new()));
    let (l, r) = (levels.clone(), reports.clone());
    let parts = LevelWatcherParts {
        apply: Arc::new(move |level| l.lock().unwrap().push(level)),
        report: Arc::new(move |message| r.lock().unwrap().push(message.to_string())),
    };
    (levels, reports, parts)
}

struct LevelWatcherParts {
    apply: Arc<dyn Fn(Level) + Send + Sync>,
    report: Arc<dyn Fn(&str) + Send + Sync>,
}

fn scratch(name: &str) -> std::path::PathBuf {
    let dir =
        std::env::temp_dir().join(format!("logquill-lifecycle-{}-{name}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

#[test]
fn level_settings_are_names_or_numbers_and_empty_means_nothing() {
    assert_eq!(parse_level_setting(" warn "), Some(Ok(Level::Warn)));
    assert_eq!(parse_level_setting("20"), Some(Ok(Level::Info)));
    assert_eq!(parse_level_setting("25"), Some(Err(())));
    assert_eq!(parse_level_setting("loud"), Some(Err(())));
    assert_eq!(parse_level_setting("   "), None);
}

#[test]
fn a_file_watcher_applies_the_first_value_and_then_only_changes() {
    let path = scratch("file").join("level");
    let (levels, reports, parts) = collectors();
    let watcher = LevelWatcher::new(
        LevelSource::File(path.clone()),
        "app",
        Duration::from_secs(60),
        parts.apply,
        parts.report,
    );
    watcher.poll_once();
    assert!(
        levels.lock().unwrap().is_empty(),
        "a missing file means no change"
    );

    fs::write(&path, "debug\n").unwrap();
    watcher.poll_once();
    watcher.poll_once();
    fs::write(&path, "ERROR").unwrap();
    watcher.poll_once();
    assert_eq!(
        *levels.lock().unwrap(),
        vec![Level::Debug, Level::Error],
        "the same text is not applied twice"
    );

    fs::write(&path, "shouting").unwrap();
    watcher.poll_once();
    let reports = reports.lock().unwrap();
    assert_eq!(reports.len(), 1);
    assert!(
        reports[0].contains("ignoring")
            && reports[0].contains("shouting")
            && reports[0].contains("for logger app")
    );
    assert_eq!(
        levels.lock().unwrap().len(),
        2,
        "an invalid value changes nothing"
    );
}

#[test]
fn an_environment_watcher_reads_the_variable() {
    let name = format!("LOGQUILL_TEST_LEVEL_{}", std::process::id());
    let (levels, _, parts) = collectors();
    let watcher = LevelWatcher::new(
        LevelSource::Env(name.clone()),
        "app",
        Duration::from_secs(60),
        parts.apply,
        parts.report,
    );
    watcher.poll_once();
    assert!(levels.lock().unwrap().is_empty());
    std::env::set_var(&name, "TRACE");
    watcher.poll_once();
    std::env::remove_var(&name);
    assert_eq!(*levels.lock().unwrap(), vec![Level::Trace]);
}

#[test]
fn the_polling_thread_starts_once_picks_up_changes_and_stops_cleanly() {
    let path = scratch("thread").join("level");
    fs::write(&path, "info").unwrap();
    let (levels, _, parts) = collectors();
    let watcher = LevelWatcher::new(
        LevelSource::File(path.clone()),
        "app",
        Duration::from_millis(20),
        parts.apply,
        parts.report,
    );
    watcher.start();
    watcher.start(); // a second start does nothing
    std::thread::sleep(Duration::from_millis(100));
    fs::write(&path, "fatal").unwrap();
    std::thread::sleep(Duration::from_millis(150));
    watcher.stop();
    watcher.stop(); // safe twice
    assert_eq!(*levels.lock().unwrap(), vec![Level::Info, Level::Fatal]);
}

struct Counting {
    written: Arc<AtomicUsize>,
    flushed: Arc<AtomicUsize>,
    closed: Arc<AtomicUsize>,
    delay: Duration,
}

impl Transport for Counting {
    fn format(&self, record: &Value) -> String {
        record.to_string()
    }

    fn write(&mut self, _: &str, _: &Value) -> Result<(), String> {
        std::thread::sleep(self.delay);
        self.written.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }

    fn flush(&mut self) -> Result<(), String> {
        self.flushed.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }

    fn close(&mut self) -> Result<(), String> {
        self.closed.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

type Counters = (Arc<AtomicUsize>, Arc<AtomicUsize>, Arc<AtomicUsize>);

fn dispatcher(delay: Duration) -> (Arc<Dispatcher>, Counters) {
    let counters: Counters = Default::default();
    let transport = Counting {
        written: counters.0.clone(),
        flushed: counters.1.clone(),
        closed: counters.2.clone(),
        delay,
    };
    let options = WorkerOptions {
        max_queue_size: 1000,
        backpressure: Backpressure::DropOldest,
        ..WorkerOptions::default()
    };
    (
        Arc::new(Dispatcher::new(options, vec![Box::new(transport)]).unwrap()),
        counters,
    )
}

// The exit registry is process-wide, so the tests that use it must not overlap.
static REGISTRY_TESTS: Mutex<()> = Mutex::new(());

#[test]
fn close_all_drains_and_closes_each_registered_dispatcher_once() {
    let _serial = REGISTRY_TESTS.lock().unwrap_or_else(|p| p.into_inner());
    let (first, a) = dispatcher(Duration::ZERO);
    let (second, b) = dispatcher(Duration::ZERO);
    register(&first);
    register(&first); // registering twice is harmless
    register(&second);
    for _ in 0..10 {
        first.emit(json!({"level": "INFO"}));
        second.emit(json!({"level": "INFO"}));
    }
    let late = close_all(Duration::from_secs(5), &|_| {});
    assert_eq!(late, 0);
    assert_eq!(
        (a.0.load(Ordering::SeqCst), b.0.load(Ordering::SeqCst)),
        (10, 10)
    );
    assert_eq!(
        (a.2.load(Ordering::SeqCst), b.2.load(Ordering::SeqCst)),
        (1, 1)
    );
    close_all(Duration::from_secs(1), &|_| {});
    assert_eq!(
        a.2.load(Ordering::SeqCst),
        1,
        "a dispatcher is not closed twice"
    );
}

#[test]
fn close_all_warns_when_the_timeout_is_too_short_and_skips_dropped_dispatchers() {
    let _serial = REGISTRY_TESTS.lock().unwrap_or_else(|p| p.into_inner());
    let (slow, counters) = dispatcher(Duration::from_millis(100));
    register(&slow);
    for _ in 0..20 {
        slow.emit(json!({"level": "INFO"}));
    }
    let warnings = Arc::new(Mutex::new(Vec::new()));
    let sink = warnings.clone();
    let late = close_all(Duration::from_millis(100), &move |m| {
        sink.lock().unwrap().push(m.to_string())
    });
    assert_eq!(late, 1);
    assert!(
        warnings.lock().unwrap()[0].contains("wasn't enough to flush every queued record at exit")
    );
    drop(slow);
    drop(counters);
    assert_eq!(
        close_all(Duration::from_millis(100), &|_| {}),
        0,
        "a dropped dispatcher is forgotten"
    );
}

#[test]
fn a_serverless_handler_flushes_afterwards_even_if_it_panics_and_does_not_close() {
    let (d, counters) = dispatcher(Duration::ZERO);
    let value = with_flush(std::slice::from_ref(&d), Duration::from_secs(5), || {
        d.emit(json!({"level": "INFO"}));
        41 + 1
    });
    assert_eq!(value, 42);
    assert_eq!(
        counters.0.load(Ordering::SeqCst),
        1,
        "the record was written before the handler returned"
    );
    assert!(counters.1.load(Ordering::SeqCst) >= 1);
    assert_eq!(
        counters.2.load(Ordering::SeqCst),
        0,
        "flush, not close: a warm container reuses it"
    );

    let result = catch_unwind(AssertUnwindSafe(|| {
        with_flush(std::slice::from_ref(&d), Duration::from_secs(5), || {
            d.emit(json!({"level": "ERROR"}));
            panic!("handler failed");
        })
    }));
    assert!(
        result.is_err(),
        "the handler's panic still reaches the caller"
    );
    assert_eq!(
        counters.0.load(Ordering::SeqCst),
        2,
        "but the record logged before it was flushed"
    );
}
