//! `logquill trace RUN_ID --file PATH`: one run's span tree, rebuilt from a log file.

use std::io::Write;
use std::path::Path;

use logquill_core::{build_trace, render_tree_with, Trace};
use serde_json::Value;

use crate::lines::py_repr;
use crate::render::paint;
use crate::source::{collect_run, Source};

/// The tree of a run in `source`, or why there is none. Only that run's records are kept in memory.
pub fn load(source: &Source, run_id: &str, command: Option<&str>) -> Result<Trace, String> {
    let records = collect_run(source, run_id, command)?;
    Ok(build_trace(records.iter(), run_id))
}

/// Runs the command; returns the exit code.
pub fn run(run_id: &str, path: &Path, json: bool, colour: bool, out: &mut dyn Write) -> u8 {
    if !path.exists() {
        eprintln!("logquill trace: no such file: {}", path.display());
        return 1;
    }
    let trace = match load(&Source::Jsonl(path.to_path_buf()), run_id, Some("trace")) {
        Ok(trace) => trace,
        Err(e) => {
            eprintln!("logquill trace: {e}");
            return 1;
        }
    };
    if trace.record_count == 0 {
        eprintln!(
            "logquill trace: no records with meta.run_id={} found in {} \u{2014} check the run id, or that this file's records carry run_id at all (see RunPlugin)",
            py_repr(run_id),
            path.display()
        );
        return 1;
    }
    let text = if json {
        let roots: Vec<Value> = trace.roots.iter().map(|n| n.to_node_value()).collect();
        Value::Array(roots).to_string()
    } else {
        render_tree_with(&trace.roots, &|level, label| paint(level, label, colour))
    };
    let _ = writeln!(out, "{text}");
    0
}
