//! How a record becomes a SQL row or a graph node (`docs/spec/05-runtime-and-transports.md`;
//! decision D33).

use serde_json::{json, Map, Value};

use crate::jsonx::json_line;

/// The statement every graph backend runs: one multi-row `UNWIND` that merges nodes, sessions and
/// parent edges.
pub const CYPHER: &str = "UNWIND $rows AS row MERGE (n:LogRecord {key: row.key}) SET n += row.props FOREACH (_ IN CASE WHEN row.session_key IS NULL THEN [] ELSE [1] END |   MERGE (s:Session {key: row.session_key})   MERGE (s)-[:HAS_RECORD]->(n) ) FOREACH (_ IN CASE WHEN row.parent_key IS NULL THEN [] ELSE [1] END |   MERGE (p:LogRecord {key: row.parent_key})   MERGE (p)-[:PARENT_OF]->(n) )";

/// The SQL dialects the table definition is written for.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Dialect {
    Sqlite,
    Postgres,
    Mysql,
}

/// `CREATE TABLE IF NOT EXISTS` for the fixed log table in a dialect.
pub fn create_table_sql(dialect: Dialect, table: &str) -> String {
    let (key, level, json_type) = match dialect {
        Dialect::Sqlite => ("INTEGER PRIMARY KEY AUTOINCREMENT", "TEXT", "TEXT"),
        Dialect::Postgres => ("SERIAL PRIMARY KEY", "TEXT", "JSONB"),
        Dialect::Mysql => ("INT AUTO_INCREMENT PRIMARY KEY", "VARCHAR(16)", "JSON"),
    };
    format!(
        "CREATE TABLE IF NOT EXISTS {table} (id {key}, timestamp TEXT NOT NULL, level {level} NOT NULL, \
         logger TEXT NOT NULL, message TEXT NOT NULL, meta {json_type} NOT NULL, llm {json_type}, \
         run_id TEXT, span_id TEXT, parent_span_id TEXT, trace_id TEXT)"
    )
}

fn text(meta: &Map<String, Value>, key: &str) -> Value {
    match meta.get(key) {
        Some(Value::String(s)) => Value::String(s.clone()),
        _ => Value::Null,
    }
}

/// The columns of one log row, in table order. `llm` is `null` when the record has no block.
pub fn sql_row(record: &Value) -> Value {
    let empty = Map::new();
    let meta = record
        .get("meta")
        .and_then(Value::as_object)
        .unwrap_or(&empty);
    let llm = match record.get("llm") {
        Some(llm @ Value::Object(map)) if !map.is_empty() => Value::String(json_line(llm)),
        _ => Value::Null,
    };
    json!({
        "timestamp": record.get("timestamp"),
        "level": record.get("level"),
        "logger": record.get("logger"),
        "message": record.get("message"),
        "meta": json_line(&Value::Object(meta.clone())),
        "llm": llm,
        "run_id": text(meta, "run_id"),
        "span_id": text(meta, "span_id"),
        "parent_span_id": text(meta, "parent_span_id"),
        "trace_id": text(meta, "trace_id"),
    })
}

/// Which `meta` keys name the session a graph node belongs to.
#[derive(Clone, Debug)]
pub struct GraphKeys {
    pub run_key: String,
    pub trace_key: String,
}

impl Default for GraphKeys {
    fn default() -> Self {
        GraphKeys {
            run_key: "run_id".into(),
            trace_key: "trace_id".into(),
        }
    }
}

/// A node key: the span id, or a placeholder the caller replaces with a fresh unique id.
pub const LEAF_PLACEHOLDER: &str = "leaf-<random>";

fn non_empty<'a>(meta: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    match meta.get(key) {
        Some(Value::String(s)) if !s.is_empty() => Some(s),
        _ => None,
    }
}

/// One record as a graph node: its key, properties, session and parent. A record with no span id
/// of its own gets [`LEAF_PLACEHOLDER`] as key; a parent equal to the key is dropped.
pub fn graph_row(record: &Value, keys: &GraphKeys) -> Value {
    let empty = Map::new();
    let meta = record
        .get("meta")
        .and_then(Value::as_object)
        .unwrap_or(&empty);
    let key = non_empty(meta, "span_id").unwrap_or(LEAF_PLACEHOLDER);
    let session = non_empty(meta, &keys.run_key).or_else(|| non_empty(meta, &keys.trace_key));
    let parent = non_empty(meta, "parent_span_id").filter(|p| *p != key);
    let mut props = Map::new();
    for field in ["timestamp", "level", "logger", "message"] {
        props.insert(
            field.into(),
            record.get(field).cloned().unwrap_or(Value::Null),
        );
    }
    props.insert(
        "meta".into(),
        Value::String(json_line(&Value::Object(meta.clone()))),
    );
    for field in ["run_id", "trace_id", "span_id", "parent_span_id"] {
        if let Some(value) = non_empty(meta, field) {
            props.insert(field.into(), value.into());
        }
    }
    json!({"key": key, "props": props, "session_key": session, "parent_key": parent})
}
