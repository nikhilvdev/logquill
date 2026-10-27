//! `logquill dev FILE`: a live view of one run's span tree, redrawn as the file grows.
//!
//! Unlike `trace`, this keeps every record that has a run id for the whole session, because the
//! tracked run can switch when a newer run starts.

use std::cell::Cell;
use std::collections::VecDeque;
use std::io::Write;
use std::path::Path;
use std::thread;
use std::time::Duration;

use logquill_core::{build_trace, render_tree_with};
use serde_json::Value;

use crate::follow::Follower;
use crate::lines::parse_line;
use crate::render::paint;
use crate::source::run_of;

pub struct Options {
    /// Track this run and never switch away from it.
    pub run_id: Option<String>,
    pub colour: bool,
    /// How many of the file's last lines seed the view.
    pub backlog: usize,
}

/// One screen: with colour, clear the terminal first; then a heading and the run's tree.
pub fn frame(records: &[Value], run_id: &str, colour: bool) -> String {
    let trace = build_trace(records.iter(), run_id);
    let tree = render_tree_with(&trace.roots, &|level, label| paint(level, label, colour));
    let clear = if colour { "\x1b[2J\x1b[H" } else { "" };
    format!("{clear}logquill dev \u{2014} run {run_id}\n\n{tree}\n")
}

/// Runs the command until interrupted; returns the exit code.
pub fn run(path: &Path, options: &Options, out: &mut dyn Write) -> u8 {
    if !path.exists() {
        eprintln!("logquill dev: no such file: {}", path.display());
        return 1;
    }
    let mut follower = Follower::new(path);
    let mut recent: VecDeque<String> = VecDeque::new();
    if let Err(e) = follower.poll(|line| {
        recent.push_back(line.to_string());
        if recent.len() > options.backlog {
            recent.pop_front();
        }
    }) {
        eprintln!(
            "logquill dev: couldn't read {} \u{2014} {e}",
            path.display()
        );
        return 1;
    }
    let mut records: Vec<Value> = Vec::new();
    let mut tracked = options.run_id.clone();
    let changed = Cell::new(false);
    let take = |line: &str, records: &mut Vec<Value>, tracked: &mut Option<String>| {
        let Some(record) = parse_line(line, Some("dev")) else {
            return;
        };
        let Some(id) = run_of(&record).map(str::to_string) else {
            return;
        };
        if options.run_id.is_none() {
            *tracked = Some(id);
        }
        records.push(record);
        changed.set(true);
    };
    for line in recent {
        take(&line, &mut records, &mut tracked);
    }
    loop {
        if changed.replace(false) {
            if let Some(id) = &tracked {
                let screen = frame(&records, id, options.colour);
                if write!(out, "{screen}").is_err() || out.flush().is_err() {
                    return 0;
                }
            }
        }
        thread::sleep(Duration::from_millis(500));
        if let Err(e) = follower.poll(|line| take(line, &mut records, &mut tracked)) {
            eprintln!(
                "logquill dev: couldn't read {} \u{2014} {e}",
                path.display()
            );
            return 1;
        }
    }
}
