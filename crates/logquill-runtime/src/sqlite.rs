//! The SQLite sink (`docs/spec/05-runtime-and-transports.md`; decision D33). Also what the local
//! viewer reads. One multi-row `INSERT` per batch inside a transaction, never one per record.

use logquill_core::{create_table_sql, sql_row, Dialect};
use rusqlite::{params_from_iter, Connection};
use serde_json::Value;

use crate::batching::{BatchSender, Batching, ToItem};
use crate::transport::Formatter;

const COLUMNS: [&str; 10] = [
    "timestamp",
    "level",
    "logger",
    "message",
    "meta",
    "llm",
    "run_id",
    "span_id",
    "parent_span_id",
    "trace_id",
];

/// `name` or `schema.name`, letters, digits and underscores only.
fn valid_table_name(name: &str) -> bool {
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

/// Settings for [`sqlite_transport`].
#[derive(Clone, Debug)]
pub struct SqliteOptions {
    /// A file path, or `:memory:`.
    pub filename: String,
    pub table_name: String,
    /// Create the table on first use. For development only: production schemas are the caller's.
    pub ensure_schema: bool,
    pub max_records: usize,
    pub max_bytes: usize,
}

impl Default for SqliteOptions {
    fn default() -> Self {
        SqliteOptions {
            filename: ":memory:".into(),
            table_name: "logs".into(),
            ensure_schema: false,
            max_records: 100,
            max_bytes: 1_000_000,
        }
    }
}

struct Inserter {
    connection: Connection,
    insert: String,
    create: String,
    ensure_schema: bool,
    schema_done: bool,
}

/// Rows per statement, kept well under SQLite's variable limit.
const ROWS_PER_STATEMENT: usize = 50;

impl BatchSender<Value> for Inserter {
    fn send(&mut self, batch: &[Value]) -> Result<(), String> {
        if self.ensure_schema && !self.schema_done {
            self.schema_done = true; // set first, so a failed attempt is not repeated for every batch
            self.connection
                .execute_batch(&self.create)
                .map_err(|e| format!("SQLiteTransport: creating the table failed \u{2014} {e}"))?;
        }
        let failed = |e: rusqlite::Error| {
            format!(
                "SQLiteTransport: inserting {} row(s) failed \u{2014} {e}",
                batch.len()
            )
        };
        let transaction = self.connection.transaction().map_err(failed)?;
        for chunk in batch.chunks(ROWS_PER_STATEMENT) {
            let row = format!("({})", vec!["?"; COLUMNS.len()].join(","));
            let sql = format!(
                "{} VALUES {}",
                self.insert,
                vec![row; chunk.len()].join(",")
            );
            let values: Vec<rusqlite::types::Value> = chunk
                .iter()
                .flat_map(|item| {
                    COLUMNS.iter().map(move |column| match item.get(*column) {
                        Some(Value::String(s)) => rusqlite::types::Value::Text(s.clone()),
                        _ => rusqlite::types::Value::Null,
                    })
                })
                .collect();
            transaction
                .execute(&sql, params_from_iter(values))
                .map_err(failed)?;
        }
        transaction.commit().map_err(failed)
    }
}

/// A transport that writes batches of rows to a SQLite table.
pub fn sqlite_transport(options: SqliteOptions) -> Result<Batching<Value>, String> {
    if !valid_table_name(&options.table_name) {
        return Err(format!(
            "SQLiteTransport: table_name {} is not a plain SQL identifier \u{2014} use letters, digits and underscores (optionally schema.table)",
            options.table_name
        ));
    }
    let connection = Connection::open(&options.filename).map_err(|e| {
        format!(
            "SQLiteTransport: couldn't open {} \u{2014} {e}",
            options.filename
        )
    })?;
    if options.filename != ":memory:" {
        // Write-ahead logging lets the viewer read the file while this transport writes to it.
        // Best effort: some file systems cannot do it, and the transport works without.
        let _ = connection.query_row("PRAGMA journal_mode=WAL", [], |_| Ok(()));
    }
    let inserter = Inserter {
        connection,
        insert: format!(
            "INSERT INTO {} ({})",
            options.table_name,
            COLUMNS.join(", ")
        ),
        create: create_table_sql(Dialect::Sqlite, &options.table_name),
        ensure_schema: options.ensure_schema,
        schema_done: false,
    };
    let to_item: ToItem<Value> = Box::new(|_, record| {
        let row = sql_row(record);
        let size = row
            .get("message")
            .and_then(Value::as_str)
            .map_or(0, str::len)
            + row.get("meta").and_then(Value::as_str).map_or(0, str::len)
            + 96;
        (row, size)
    });
    Ok(Batching::new(
        Formatter::Json,
        to_item,
        Box::new(inserter),
        options.max_records,
        options.max_bytes,
    ))
}
