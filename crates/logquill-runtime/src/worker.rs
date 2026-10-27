//! The dispatch queue and its one background worker (`docs/spec/05-runtime-and-transports.md`;
//! decisions D7, D25).
//!
//! Work items are closures run in order on a single thread. The queue is bounded, and what
//! happens when it is full is the caller's choice: evict the oldest item, discard the new one, or
//! make the producer wait. Memory is therefore set by `max_queue_size`, never by a slow sink.

use std::collections::VecDeque;
use std::fmt;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::str::FromStr;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

/// What to do with a submission when the queue is full.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Default)]
pub enum Backpressure {
    /// Evict the oldest queued item and queue the new one.
    #[default]
    DropOldest,
    /// Discard the new item.
    DropNewest,
    /// Make the submitting thread wait for room (or for the worker to close).
    Block,
}

impl Backpressure {
    /// The name used in configuration.
    pub const fn name(self) -> &'static str {
        match self {
            Backpressure::DropOldest => "drop_oldest",
            Backpressure::DropNewest => "drop_newest",
            Backpressure::Block => "block",
        }
    }
}

impl FromStr for Backpressure {
    type Err = String;

    fn from_str(text: &str) -> Result<Self, String> {
        match text {
            "drop_oldest" => Ok(Backpressure::DropOldest),
            "drop_newest" => Ok(Backpressure::DropNewest),
            "block" => Ok(Backpressure::Block),
            other => Err(format!(
                "backpressure must be one of (\"drop_oldest\", \"drop_newest\", \"block\"), got {}",
                serde_json::Value::String(other.to_string())
            )),
        }
    }
}

type Task = Box<dyn FnOnce() + Send>;
type Warn = Arc<dyn Fn(&str) + Send + Sync>;

/// Settings for a [`Worker`].
#[derive(Clone)]
pub struct WorkerOptions {
    /// Most items queued at once (at least 1).
    pub max_queue_size: usize,
    pub backpressure: Backpressure,
    /// How often the "queue full" warning may be repeated.
    pub warn_interval: Duration,
    /// Receives warnings (queue full, a task that panicked). Must not log through this worker.
    pub warn: Warn,
}

impl Default for WorkerOptions {
    fn default() -> Self {
        WorkerOptions {
            max_queue_size: 10_000,
            backpressure: Backpressure::DropOldest,
            warn_interval: Duration::from_secs(60),
            warn: Arc::new(|message| eprintln!("logquill: {message}")),
        }
    }
}

impl fmt::Debug for WorkerOptions {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("WorkerOptions")
            .field("max_queue_size", &self.max_queue_size)
            .field("backpressure", &self.backpressure)
            .field("warn_interval", &self.warn_interval)
            .finish_non_exhaustive()
    }
}

/// Counters, for tests and diagnostics.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Stats {
    /// Items waiting to run.
    pub queued: usize,
    /// Items accepted (including ones later evicted).
    pub submitted: u64,
    /// Items that ran, panicked, or were evicted.
    pub finished: u64,
    /// Items discarded because the queue was full.
    pub dropped: u64,
}

struct State {
    queue: VecDeque<Task>,
    closed: bool,
    thread_done: bool,
    stats: Stats,
    dropped_since_warning: u64,
    last_warning: Option<Instant>,
    /// Who is asleep on which condition variable. A signal is sent only when someone is, because
    /// on Linux every signal is a system call, and a busy queue would pay for one per record.
    worker_waiting: bool,
    space_waiters: usize,
    idle_waiters: usize,
}

struct Shared {
    state: Mutex<State>,
    /// The worker waits here for work.
    work: Condvar,
    /// Producers blocked on a full queue wait here.
    space: Condvar,
    /// `drain` and `close` wait here for items to finish.
    idle: Condvar,
}

fn lock(shared: &Shared) -> MutexGuard<'_, State> {
    shared
        .state
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
}

impl Shared {
    fn new() -> Arc<Shared> {
        Arc::new(Shared {
            state: Mutex::new(State {
                queue: VecDeque::new(),
                closed: false,
                thread_done: false,
                stats: Stats::default(),
                dropped_since_warning: 0,
                last_warning: None,
                worker_waiting: false,
                space_waiters: 0,
                idle_waiters: 0,
            }),
            work: Condvar::new(),
            space: Condvar::new(),
            idle: Condvar::new(),
        })
    }
}

fn run(shared: &Shared, warn: &Warn) {
    let mut finished_one = false;
    loop {
        let task = {
            let mut state = lock(shared);
            // The previous task is accounted for here, under the lock taken to fetch the next one.
            if std::mem::take(&mut finished_one) {
                state.stats.finished += 1;
                if state.idle_waiters > 0 {
                    shared.idle.notify_all();
                }
            }
            loop {
                if let Some(task) = state.queue.pop_front() {
                    if state.space_waiters > 0 {
                        shared.space.notify_one();
                    }
                    break Some(task);
                }
                if state.closed {
                    break None;
                }
                state.worker_waiting = true;
                state = shared.work.wait(state).unwrap_or_else(|p| p.into_inner());
                state.worker_waiting = false;
            }
        };
        let Some(task) = task else { break };
        if catch_unwind(AssertUnwindSafe(task)).is_err() {
            warn("a queued task panicked; the worker carries on");
        }
        finished_one = true;
    }
    let mut state = lock(shared);
    state.thread_done = true;
    shared.idle.notify_all();
}

/// A bounded FIFO of closures run by one background thread.
pub struct Worker {
    options: WorkerOptions,
    pid: Arc<dyn Fn() -> u32 + Send + Sync>,
    spawned_for: AtomicU32,
    current: Mutex<(Arc<Shared>, Option<JoinHandle<()>>)>,
}

impl Worker {
    /// Starts the worker, or says why the options are refused.
    pub fn new(options: WorkerOptions) -> Result<Worker, String> {
        Worker::with_process_id(options, Arc::new(std::process::id))
    }

    /// Like [`Worker::new`] with the process id taken from `pid`, so a test can act out a `fork`.
    pub fn with_process_id(
        options: WorkerOptions,
        pid: Arc<dyn Fn() -> u32 + Send + Sync>,
    ) -> Result<Worker, String> {
        if options.max_queue_size < 1 {
            return Err(format!(
                "max_queue_size must be at least 1, got {}",
                options.max_queue_size
            ));
        }
        let shared = Shared::new();
        let handle = Worker::spawn(&shared, &options.warn);
        Ok(Worker {
            spawned_for: AtomicU32::new(pid()),
            pid,
            options,
            current: Mutex::new((shared, Some(handle))),
        })
    }

    fn spawn(shared: &Arc<Shared>, warn: &Warn) -> JoinHandle<()> {
        let (shared, warn) = (shared.clone(), warn.clone());
        thread::Builder::new()
            .name("logquill-worker".into())
            .spawn(move || run(&shared, &warn))
            .expect("the operating system refused to start a thread")
    }

    /// After a `fork` only the forking thread exists in the child, so the worker thread is gone
    /// and its lock may be held forever. Detect that and start over with fresh state (D25).
    fn shared(&self) -> Arc<Shared> {
        let mut current = self.current.lock().unwrap_or_else(|p| p.into_inner());
        let now = (self.pid)();
        if self.spawned_for.load(Ordering::SeqCst) != now {
            self.spawned_for.store(now, Ordering::SeqCst);
            let fresh = Shared::new();
            let handle = Worker::spawn(&fresh, &self.options.warn);
            *current = (fresh, Some(handle));
        }
        current.0.clone()
    }

    fn note_drop(&self, shared: &Shared) {
        let message = {
            let mut state = lock(shared);
            state.stats.dropped += 1;
            state.dropped_since_warning += 1;
            let due = state
                .last_warning
                .map_or(true, |at| at.elapsed() >= self.options.warn_interval);
            if !due {
                return;
            }
            let count = std::mem::take(&mut state.dropped_since_warning);
            state.last_warning = Some(Instant::now());
            format!(
                "AsyncWorker: queue full at max_queue_size={}, dropping records under backpressure={} \
                 \u{2014} the consumer (a transport) isn't keeping up, or max_queue_size is set too low \
                 for this burst rate ({count} dropped since the last warning)",
                self.options.max_queue_size,
                self.options.backpressure.name(),
            )
        };
        (self.options.warn)(&message);
    }

    /// Queues `task`. Returns whether it was accepted; after `close` a submission is silently
    /// refused. Under `drop_oldest` an accepted item may evict an older one.
    pub fn submit(&self, task: impl FnOnce() + Send + 'static) -> bool {
        let shared = self.shared();
        let mut state = lock(&shared);
        if state.closed {
            return false;
        }
        let max = self.options.max_queue_size;
        while state.queue.len() >= max {
            match self.options.backpressure {
                Backpressure::DropNewest => {
                    drop(state);
                    self.note_drop(&shared);
                    return false;
                }
                Backpressure::DropOldest => {
                    state.queue.pop_front();
                    state.stats.finished += 1;
                    if state.idle_waiters > 0 {
                        shared.idle.notify_all();
                    }
                    drop(state);
                    self.note_drop(&shared);
                    state = lock(&shared);
                    if state.closed {
                        return false;
                    }
                }
                Backpressure::Block => {
                    state.space_waiters += 1;
                    state = shared.space.wait(state).unwrap_or_else(|p| p.into_inner());
                    state.space_waiters -= 1;
                    if state.closed {
                        return false;
                    }
                }
            }
        }
        state.queue.push_back(Box::new(task));
        state.stats.submitted += 1;
        if state.worker_waiting {
            shared.work.notify_one();
        }
        true
    }

    /// Waits until everything submitted so far has finished. `None` waits forever. Returns
    /// whether it finished in time.
    pub fn drain(&self, timeout: Option<Duration>) -> bool {
        let shared = self.shared();
        let deadline = timeout.map(|t| Instant::now() + t);
        let mut state = lock(&shared);
        let target = state.stats.submitted;
        while state.stats.finished < target {
            let wait = match deadline {
                None => None,
                Some(deadline) => match deadline.checked_duration_since(Instant::now()) {
                    Some(left) => Some(left),
                    None => return false,
                },
            };
            state.idle_waiters += 1;
            state = match wait {
                None => shared.idle.wait(state).unwrap_or_else(|p| p.into_inner()),
                Some(left) => {
                    shared
                        .idle
                        .wait_timeout(state, left)
                        .unwrap_or_else(|p| p.into_inner())
                        .0
                }
            };
            state.idle_waiters -= 1;
        }
        true
    }

    /// Drains, stops accepting work, wakes any waiting producer and joins the thread (for up to
    /// one second). Idempotent. Returns whether the drain finished in time.
    pub fn close(&self, timeout: Option<Duration>) -> bool {
        let drained = self.drain(timeout);
        let shared = self.shared();
        {
            let mut state = lock(&shared);
            state.closed = true;
            shared.work.notify_all();
            shared.space.notify_all();
        }
        let handle = self
            .current
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .1
            .take();
        if let Some(handle) = handle {
            let deadline = Instant::now() + Duration::from_secs(1);
            let mut state = lock(&shared);
            while !state.thread_done {
                let Some(left) = deadline.checked_duration_since(Instant::now()) else {
                    return drained; // still busy with a slow task: leave the thread to finish alone
                };
                state.idle_waiters += 1;
                state = shared
                    .idle
                    .wait_timeout(state, left)
                    .unwrap_or_else(|p| p.into_inner())
                    .0;
                state.idle_waiters -= 1;
            }
            drop(state);
            let _ = handle.join();
        }
        drained
    }

    /// Current counters.
    pub fn stats(&self) -> Stats {
        let shared = self.shared();
        let state = lock(&shared);
        Stats {
            queued: state.queue.len(),
            ..state.stats
        }
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        self.close(Some(Duration::from_secs(5)));
    }
}
