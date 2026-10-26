//! Runtime level control: watchers that poll a file or an environment variable and hand a changed
//! level to the application (`docs/spec/04-formatters-config-parsing.md`, "Runtime level control").
//!
//! A watcher applies the value when the raw text changes, applies the first value it sees, treats
//! a missing file as "no change", and never raises over a bad value: it reports it and carries on.

use std::fs;
use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use logquill_core::Level;

/// Called with each new valid level.
pub type Apply = Arc<dyn Fn(Level) + Send + Sync>;
/// Called with human-readable complaints (an invalid level).
pub type Report = Arc<dyn Fn(&str) + Send + Sync>;

/// Where the raw level text comes from.
pub enum LevelSource {
    /// The trimmed contents of a file; a missing file means no change.
    File(PathBuf),
    /// An environment variable.
    Env(String),
}

impl LevelSource {
    fn describe(&self) -> String {
        match self {
            LevelSource::File(path) => path.display().to_string(),
            LevelSource::Env(name) => name.clone(),
        }
    }

    fn read(&self) -> Option<String> {
        match self {
            LevelSource::File(path) => fs::read_to_string(path).ok(),
            LevelSource::Env(name) => std::env::var(name).ok(),
        }
    }
}

/// Reads a level string: names in any case, or a numeric weight. Empty is "no value".
pub fn parse_level_setting(raw: &str) -> Option<Result<Level, ()>> {
    let text = raw.trim();
    if text.is_empty() {
        return None;
    }
    let parsed = match text.parse::<i64>() {
        Ok(weight) => Level::from_weight(weight),
        Err(_) => Level::parse(text),
    };
    Some(parsed.map_err(|_| ()))
}

struct State {
    last: Option<String>,
    stop: bool,
}

/// Polls a [`LevelSource`] and applies changes.
pub struct LevelWatcher {
    source: LevelSource,
    logger: String,
    interval: Duration,
    apply: Apply,
    report: Report,
    state: Arc<(Mutex<State>, Condvar)>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl LevelWatcher {
    /// A watcher that polls every `interval` (2 seconds is the usual choice).
    pub fn new(
        source: LevelSource,
        logger: &str,
        interval: Duration,
        apply: Apply,
        report: Report,
    ) -> Arc<LevelWatcher> {
        Arc::new(LevelWatcher {
            source,
            logger: logger.to_string(),
            interval,
            apply,
            report,
            state: Arc::new((
                Mutex::new(State {
                    last: None,
                    stop: false,
                }),
                Condvar::new(),
            )),
            thread: Mutex::new(None),
        })
    }

    /// Looks once. Applies the level if the raw text differs from the last one seen.
    pub fn poll_once(&self) {
        let Some(raw) = self.source.read() else {
            return;
        };
        {
            let mut state = self.state.0.lock().unwrap_or_else(|p| p.into_inner());
            if state.last.as_deref() == Some(raw.as_str()) {
                return;
            }
            state.last = Some(raw.clone());
        }
        match parse_level_setting(&raw) {
            None => {}
            Some(Ok(level)) => (self.apply)(level),
            Some(Err(())) => (self.report)(&format!(
                "logquill: ignoring {}={} for logger {} \u{2014} not a valid level name (TRACE, DEBUG, INFO, WARN, ERROR, FATAL) or number",
                self.source.describe(),
                raw.trim(),
                self.logger
            )),
        }
    }

    /// Starts polling on a background thread. Calling it again does nothing.
    pub fn start(self: &Arc<Self>) {
        let mut slot = self.thread.lock().unwrap_or_else(|p| p.into_inner());
        if slot.is_some() {
            return;
        }
        self.state.0.lock().unwrap_or_else(|p| p.into_inner()).stop = false;
        let watcher = self.clone();
        *slot = thread::Builder::new()
            .name("logquill-level-watcher".into())
            .spawn(move || loop {
                watcher.poll_once();
                let (lock, wake) = &*watcher.state;
                let guard = lock.lock().unwrap_or_else(|p| p.into_inner());
                if guard.stop {
                    return;
                }
                let (guard, _) = wake
                    .wait_timeout(guard, watcher.interval)
                    .unwrap_or_else(|p| p.into_inner());
                if guard.stop {
                    return;
                }
            })
            .ok();
    }

    /// Stops polling. Safe to call when not started, and more than once.
    pub fn stop(&self) {
        {
            let (lock, wake) = &*self.state;
            lock.lock().unwrap_or_else(|p| p.into_inner()).stop = true;
            wake.notify_all();
        }
        if let Some(handle) = self.thread.lock().unwrap_or_else(|p| p.into_inner()).take() {
            let _ = handle.join();
        }
    }
}
