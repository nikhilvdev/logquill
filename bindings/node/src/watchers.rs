//! Runtime level control for Node: a watcher that polls a file or an environment variable from a Rust
//! thread and calls back into JavaScript when the level changes, so changing a logger's level needs no
//! restart and the polling never touches the event loop.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use logquill_runtime::{LevelSource, LevelWatcher as RuntimeWatcher};
use napi::bindgen_prelude::Function;
use napi::threadsafe_function::{ThreadsafeFunction, ThreadsafeFunctionCallMode};
use napi::{Error, Result};
use napi_derive::napi;

type Callback = ThreadsafeFunction<String, (), String, napi::Status, false, true>;

/// Options for `LevelWatcher`: exactly one of `file` and `env`.
#[napi(object)]
pub struct WatcherConfig {
    /// A file whose trimmed contents are the level; a missing file means no change.
    pub file: Option<String>,
    /// An environment variable holding the level.
    pub env: Option<String>,
    /// Named in complaints about an invalid value (default `logquill`).
    pub logger: Option<String>,
    /// Seconds between looks (default 2).
    pub interval_seconds: Option<f64>,
}

/// Polls a level source on a background thread and calls `apply(levelName)` whenever it changes to a valid
/// level; an invalid value goes to `report(message)` (or standard error) and the level is left alone.
#[napi]
pub struct LevelWatcher {
    inner: Arc<RuntimeWatcher>,
}

#[napi]
impl LevelWatcher {
    #[napi(constructor)]
    pub fn new(
        config: WatcherConfig,
        apply: Function<'_, String, ()>,
        report: Option<Function<'_, String, ()>>,
    ) -> Result<LevelWatcher> {
        let source = match (config.file, config.env) {
            (Some(path), None) => LevelSource::File(PathBuf::from(path)),
            (None, Some(name)) => LevelSource::Env(name),
            _ => {
                return Err(Error::from_reason(
                    "a LevelWatcher needs exactly one of `file` and `env`".to_string(),
                ))
            }
        };
        // weak: a watcher must not keep the process alive on its own
        let apply: Arc<Callback> = Arc::new(
            apply
                .build_threadsafe_function::<String>()
                .callee_handled::<false>()
                .weak::<true>()
                .build()?,
        );
        let report: Option<Arc<Callback>> = match report {
            Some(function) => Some(Arc::new(
                function
                    .build_threadsafe_function::<String>()
                    .callee_handled::<false>()
                    .weak::<true>()
                    .build()?,
            )),
            None => None,
        };
        let inner = RuntimeWatcher::new(
            source,
            config.logger.as_deref().unwrap_or("logquill"),
            Duration::from_secs_f64(config.interval_seconds.unwrap_or(2.0).max(0.01)),
            Arc::new(move |level| {
                apply.call(
                    level.name().to_string(),
                    ThreadsafeFunctionCallMode::NonBlocking,
                );
            }),
            Arc::new(move |message| match &report {
                Some(callback) => {
                    callback.call(message.to_string(), ThreadsafeFunctionCallMode::NonBlocking);
                }
                None => eprintln!("{message}"),
            }),
        );
        Ok(LevelWatcher { inner })
    }

    /// Looks once, now, on the calling thread.
    #[napi]
    pub fn poll_once(&self) {
        self.inner.poll_once();
    }

    /// Starts polling on a background thread. Calling it again does nothing.
    #[napi]
    pub fn start(&self) {
        self.inner.start();
    }

    /// Stops polling. Safe to call when not started, and more than once.
    #[napi]
    pub fn stop(&self) {
        self.inner.stop();
    }
}
