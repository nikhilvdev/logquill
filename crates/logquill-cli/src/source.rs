//! Where `serve` (and `trace`) read records from: a JSONL file, or the table the SQLite transport
//! writes. Both are streamed, so memory does not grow with the size of the log.

use std::fs::File;
use std::ops::ControlFlow;
use std::path::PathBuf;

use rusqlite::{Connection, OpenFlags};
use serde_json::{json, Map, Value};

use crate::lines::{for_each_line, parse_line};

pub enum Source {
    Jsonl(PathBuf),
    Sqlite { path: PathBuf, table: String },
}

/// `name` or `schema.name`, letters, digits and underscores only: the table name goes into SQL
/// text, so anything else is refused.
pub fn valid_table_name(name: &str) -> bool {
    let part = |p: &str| {
        let mut chars = p.chars();
        chars
            .next()
            .is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
            && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
    };
    let mut pieces = name.split('.');
    let first = pieces.next().is_some_and(part);
    let second = pieces.next();
    first && second.map_or(true, part) && pieces.next().is_none()
}

impl Source {
    pub fn path(&self) -> &PathBuf {
        match self {
            Source::Jsonl(path) | Source::Sqlite { path, .. } => path,
        }
    }

    /// Calls `f` with each record in order until it breaks. `command` names the caller in warnings
    /// about malformed JSONL lines; `None` skips them silently.
    pub fn for_each(
        &self,
        command: Option<&str>,
        f: &mut dyn FnMut(Value) -> ControlFlow<()>,
    ) -> Result<(), String> {
        match self {
            Source::Jsonl(path) => {
                let file = File::open(path)
                    .map_err(|e| format!("couldn't open {} \u{2014} {e}", path.display()))?;
                for_each_line(file, |line| match parse_line(line, command) {
                    Some(record) => f(record),
                    None => ControlFlow::Continue(()),
                })
                .map_err(|e| format!("couldn't read {} \u{2014} {e}", path.display()))
            }
            Source::Sqlite { path, table } => {
                if !valid_table_name(table) {
                    return Err(format!(
                        "table {table} is not a plain SQL identifier \u{2014} use letters, digits and underscores"
                    ));
                }
                let fail =
                    |e: rusqlite::Error| format!("couldn't read {} \u{2014} {e}", path.display());
                let connection =
                    Connection::open_with_flags(path, OpenFlags::SQLITE_OPEN_READ_ONLY)
                        .map_err(fail)?;
                let mut statement = connection
                    .prepare(&format!(
                        "SELECT timestamp, level, logger, message, meta, run_id, span_id, \
                         parent_span_id, trace_id FROM {table} ORDER BY id"
                    ))
                    .map_err(fail)?;
                let mut rows = statement.query([]).map_err(fail)?;
                while let Some(row) = rows.next().map_err(fail)? {
                    if f(row_record(row)).is_break() {
                        break;
                    }
                }
                Ok(())
            }
        }
    }
}

/// One table row as a record. The row has no `llm` column, so tokens and cost are not shown.
fn row_record(row: &rusqlite::Row) -> Value {
    let text = |i: usize| row.get::<_, Option<String>>(i).ok().flatten();
    let value = |i: usize| text(i).map_or(Value::Null, Value::String);
    let mut meta = match text(4).and_then(|t| serde_json::from_str::<Value>(&t).ok()) {
        Some(Value::Object(map)) => map,
        _ => Map::new(),
    };
    for (index, key) in [
        (5, "run_id"),
        (6, "span_id"),
        (7, "parent_span_id"),
        (8, "trace_id"),
    ] {
        if let Some(found) = text(index) {
            meta.entry(key).or_insert(Value::String(found));
        }
    }
    json!({
        "schema_version": "1.0",
        "timestamp": value(0),
        "level": value(1),
        "logger": value(2),
        "message": value(3),
        "meta": meta,
    })
}

/// The records whose `meta.run_id` is `run_id`: memory is one run, not the file.
pub fn collect_run(
    source: &Source,
    run_id: &str,
    command: Option<&str>,
) -> Result<Vec<Value>, String> {
    let mut found = Vec::new();
    source.for_each(command, &mut |record| {
        if run_of(&record) == Some(run_id) {
            found.push(record);
        }
        ControlFlow::Continue(())
    })?;
    Ok(found)
}

/// `meta.run_id` when it is a string.
pub fn run_of(record: &Value) -> Option<&str> {
    record.get("meta")?.get("run_id")?.as_str()
}
