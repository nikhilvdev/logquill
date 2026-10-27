//! The CLI reads logs as a stream: tracing, tailing and summarising a large file must use memory
//! set by what is kept (one run, N lines, one entry per run), not by the size of the file.
//!
//! The file is generated here, 32 MiB by default. Set `LOGQUILL_BIG_MB=1024` to check the full
//! ~1 GB case from the Stage 4 exit criteria (it takes a few minutes in a debug build).

use std::fs::{self, File};
use std::io::{BufWriter, Write};
use std::path::PathBuf;
use std::sync::atomic::Ordering;

use logquill_cli::runs::summarise;
use logquill_cli::source::Source;
use logquill_cli::{tail, trace};

#[path = "common/counting.rs"]
mod counting;
use counting::{LIVE, PEAK};

/// Writes about `megabytes` of records spread over 1,000 noise runs, with a five-record run
/// called `target` in the middle.
fn generate(megabytes: usize) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("logquill-memory-{}", std::process::id()));
    fs::create_dir_all(&dir).unwrap();
    let path = dir.join("big.log");
    let mut out = BufWriter::new(File::create(&path).unwrap());
    let padding = "x".repeat(180);
    let mut written = 0usize;
    let mut n = 0u64;
    let total = megabytes * 1024 * 1024;
    while written < total {
        let run = if written > total / 2 && n % 50_000 < 5 {
            "target".to_string()
        } else {
            format!("noise-{}", n % 1000)
        };
        let line = format!(
            "{{\"schema_version\":\"2.0\",\"timestamp\":\"2026-01-01T00:00:00.000Z\",\"level\":\"INFO\",\"logger\":\"app\",\"message\":\"step {n}\",\"meta\":{{\"run_id\":\"{run}\",\"pad\":\"{padding}\"}}}}\n"
        );
        out.write_all(line.as_bytes()).unwrap();
        written += line.len();
        n += 1;
    }
    out.flush().unwrap();
    path
}

/// How far live memory rose above its starting point while `work` ran.
fn growth(work: impl FnOnce()) -> usize {
    let baseline = LIVE.load(Ordering::Relaxed);
    PEAK.store(baseline, Ordering::Relaxed);
    work();
    PEAK.load(Ordering::Relaxed).saturating_sub(baseline)
}

#[test]
fn tracing_tailing_and_summarising_a_large_log_use_bounded_memory() {
    let megabytes = std::env::var("LOGQUILL_BIG_MB")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(32);
    let path = generate(megabytes);
    let file_bytes = fs::metadata(&path).unwrap().len() as usize;
    let budget = 2 * 1024 * 1024;
    assert!(
        file_bytes > 8 * budget,
        "the file must dwarf the budget for this to mean anything"
    );

    let source = Source::Jsonl(path.clone());
    let mut roots = 0;
    let traced = growth(|| {
        let trace = trace::load(&source, "target", None).unwrap();
        roots = trace.roots.len();
    });
    assert!(roots > 0, "the target run was found");
    assert!(
        traced <= budget,
        "trace grew by {traced} bytes on a {file_bytes}-byte file (budget {budget})"
    );

    let options = |lines| tail::Options {
        level: None,
        json: true,
        follow: false,
        lines,
        colour: false,
    };
    let last = growth(|| {
        assert_eq!(
            tail::run(&path, &options(Some(10)), &mut std::io::sink()),
            0
        );
    });
    assert!(
        last <= budget,
        "tail -n 10 grew by {last} bytes (budget {budget})"
    );
    let streamed = growth(|| {
        assert_eq!(tail::run(&path, &options(None), &mut std::io::sink()), 0);
    });
    assert!(
        streamed <= budget,
        "tail grew by {streamed} bytes (budget {budget})"
    );

    let mut runs = 0;
    let summarised = growth(|| runs = summarise(&source).unwrap().len());
    assert_eq!(runs, 1001, "1,000 noise runs and the target");
    assert!(
        summarised <= 4 * budget,
        "summarise grew by {summarised} bytes (budget {})",
        4 * budget
    );

    let _ = fs::remove_dir_all(path.parent().unwrap());
}
