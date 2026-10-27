//! `logquill tail FILE`: print a log's records, optionally filtered, and optionally keep following.

use std::cell::Cell;
use std::collections::VecDeque;
use std::io::Write;
use std::path::Path;
use std::thread;
use std::time::Duration;

use logquill_core::{format_json, format_text, Level};
use serde_json::Value;

use crate::follow::Follower;
use crate::lines::parse_line;
use crate::render::paint;

pub struct Options {
    /// Keep records at or above this level; a record with no valid level is dropped when set.
    pub level: Option<Level>,
    pub json: bool,
    pub follow: bool,
    /// Show only the last N matching records of what is already in the file.
    pub lines: Option<usize>,
    pub colour: bool,
}

fn keep(options: &Options, record: &Value) -> bool {
    options.level.map_or(true, |minimum| {
        record
            .get("level")
            .and_then(Value::as_str)
            .and_then(Level::from_record_name)
            .is_some_and(|level| level >= minimum)
    })
}

fn show(options: &Options, record: &Value) -> String {
    if options.json {
        format_json(record)
    } else {
        paint(
            record.get("level").unwrap_or(&Value::Null),
            format_text(record),
            options.colour,
        )
    }
}

/// Runs the command; returns the exit code. A closed output (`| head`) ends it quietly.
pub fn run(path: &Path, options: &Options, out: &mut dyn Write) -> u8 {
    if !path.exists() {
        eprintln!("logquill tail: no such file: {}", path.display());
        return 1;
    }
    let mut follower = Follower::new(path);
    let mut window: VecDeque<Value> = VecDeque::new();
    let mut live = options.lines.is_none();
    let closed = Cell::new(false);
    let mut emit = |record: Value, live: bool, window: &mut VecDeque<Value>| {
        if !keep(options, &record) {
            return;
        }
        if live {
            let failed =
                writeln!(out, "{}", show(options, &record)).is_err() || out.flush().is_err();
            closed.set(closed.get() || failed);
        } else if let Some(limit) = options.lines {
            window.push_back(record);
            if window.len() > limit {
                window.pop_front();
            }
        }
    };
    loop {
        let read = follower.poll(|line| {
            if let Some(record) = parse_line(line, Some("tail")) {
                emit(record, live, &mut window);
            }
        });
        if let Err(e) = read {
            eprintln!(
                "logquill tail: couldn't read {} \u{2014} {e}",
                path.display()
            );
            return 1;
        }
        if !live {
            live = true;
            for record in std::mem::take(&mut window) {
                emit(record, true, &mut VecDeque::new());
            }
        }
        if closed.get() || !options.follow {
            return 0;
        }
        thread::sleep(Duration::from_millis(500));
    }
}
