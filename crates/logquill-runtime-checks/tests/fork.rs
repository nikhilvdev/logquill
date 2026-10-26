//! A real `fork` (D25): the child has only the forking thread, so the parent's worker thread is
//! gone. The dispatcher must notice the new process id and start a fresh worker, or a log call
//! in the child would queue forever. Runs without the test harness so that the forking process
//! has no other threads besides the worker under test.
//!
//! Three cases: forking while idle; forking after `flush` while the transport is slow (the
//! contract a host's before-fork hook follows); and forking mid-write *without* a flush, where
//! the child inherits the transport lock held by a thread that no longer exists. Std cannot
//! release such a lock, so that case is pinned here as a known limit rather than fixed.

#[cfg(not(unix))]
fn main() {}

#[cfg(unix)]
fn main() {
    use std::fs::{self, OpenOptions};
    use std::io::Write;
    use std::path::PathBuf;
    use std::sync::Arc;
    use std::thread;
    use std::time::Duration;

    use logquill_runtime::{Backpressure, Dispatcher, Transport, WorkerOptions};
    use serde_json::{json, Value};

    /// Appends each record's message to a file, so the parent can read what the child logged.
    /// `delay` makes every write slow, which keeps the transport lock held.
    struct AppendFile(PathBuf, Duration);

    impl Transport for AppendFile {
        fn format(&self, record: &Value) -> String {
            record["message"].as_str().unwrap_or_default().to_string()
        }

        fn write(&mut self, formatted: &str, _record: &Value) -> Result<(), String> {
            thread::sleep(self.1);
            let mut file = OpenOptions::new()
                .create(true)
                .append(true)
                .open(&self.0)
                .map_err(|e| e.to_string())?;
            writeln!(file, "{formatted}").map_err(|e| e.to_string())
        }
    }

    fn start(name: &str, delay: Duration) -> (Dispatcher, PathBuf) {
        let path =
            std::env::temp_dir().join(format!("logquill-fork-{}-{name}.log", std::process::id()));
        let _ = fs::remove_file(&path);
        let options = WorkerOptions {
            max_queue_size: 100,
            backpressure: Backpressure::DropOldest,
            warn_interval: Duration::from_secs(3600),
            warn: Arc::new(|m| eprintln!("{m}")),
        };
        let transport = AppendFile(path.clone(), delay);
        (
            Dispatcher::new(options, vec![Box::new(transport)]).unwrap(),
            path,
        )
    }

    /// Forks; the child logs "child" and exits 0 if that was flushed within `timeout`, else 1.
    /// Returns the child's wait status.
    fn fork_and_log(dispatcher: &Dispatcher, timeout: Duration) -> i32 {
        // SAFETY: nothing else is running except worker threads; the child only logs and leaves
        // with `_exit`, and `alarm` turns a hang into a failure instead of a stuck CI job.
        let pid = unsafe { libc::fork() };
        assert!(pid >= 0, "fork failed");
        if pid == 0 {
            unsafe { libc::alarm(10) };
            dispatcher.emit(json!({"message": "child"}));
            let flushed = dispatcher.flush(Some(timeout));
            unsafe { libc::_exit(if flushed { 0 } else { 1 }) };
        }
        let mut status = 0;
        assert_eq!(unsafe { libc::waitpid(pid, &mut status, 0) }, pid);
        status
    }

    let long = Duration::from_secs(5);

    // 1. Idle worker.
    let (dispatcher, path) = start("idle", Duration::ZERO);
    dispatcher.emit(json!({"message": "parent"}));
    assert!(dispatcher.flush(Some(long)));
    let status = fork_and_log(&dispatcher, long);
    assert!(
        libc::WIFEXITED(status) && libc::WEXITSTATUS(status) == 0,
        "idle: the child did not flush its log call (wait status {status}): the worker did not restart after fork"
    );
    assert!(dispatcher.close(Some(long)));
    let lines = fs::read_to_string(&path).unwrap();
    assert_eq!(lines.lines().collect::<Vec<_>>(), ["parent", "child"]);
    let _ = fs::remove_file(&path);

    // 2. A slow transport, drained with `flush` before the fork: the supported way to fork.
    let (dispatcher, path) = start("drained", Duration::from_millis(30));
    for _ in 0..10 {
        dispatcher.emit(json!({"message": "parent"}));
    }
    assert!(dispatcher.flush(Some(long)), "flush before fork timed out");
    let status = fork_and_log(&dispatcher, long);
    assert!(
        libc::WIFEXITED(status) && libc::WEXITSTATUS(status) == 0,
        "drained: the child did not flush its log call (wait status {status})"
    );
    assert!(dispatcher.close(Some(long)));
    let lines = fs::read_to_string(&path).unwrap();
    assert_eq!(lines.lines().filter(|l| *l == "child").count(), 1);
    assert_eq!(lines.lines().filter(|l| *l == "parent").count(), 10);
    let _ = fs::remove_file(&path);

    // 3. Mid-write, no flush: the child's flush times out (it does not hang the process).
    let (dispatcher, path) = start("midwrite", Duration::from_millis(600));
    dispatcher.emit(json!({"message": "parent"}));
    thread::sleep(Duration::from_millis(150)); // the worker is now inside `write`, holding the lock
    let status = fork_and_log(&dispatcher, Duration::from_secs(1));
    assert!(
        libc::WIFEXITED(status) && libc::WEXITSTATUS(status) == 1,
        "mid-write: expected the child's flush to time out (exit 1), got wait status {status} \u{2014} \
         if this now succeeds the known limit is fixed: update docs/stages/stage-3.md and this test"
    );
    assert!(dispatcher.close(Some(long)));
    let _ = fs::remove_file(&path);

    println!("fork: idle and drained forks log from the child; a mid-write fork times out cleanly");
}
