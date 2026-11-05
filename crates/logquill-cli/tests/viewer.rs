//! What the viewer reads and lists: the JSONL and SQLite sources, the per-run summaries and the search.
//! These are the checks of the old Python `webui` module (`test_webui.py`) that no other test covers.

use std::fs;
use std::ops::ControlFlow;
use std::path::PathBuf;

use logquill_cli::runs::{search, summarise, Query};
use logquill_cli::source::Source;
use rusqlite::{params, Connection};
use serde_json::{json, Value};

fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("logquill-viewer-{}-{name}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).expect("a scratch directory");
    dir
}

fn record(level: &str, message: &str, timestamp: &str, meta: Value) -> Value {
    json!({"schema_version": "2.0", "timestamp": timestamp, "level": level, "logger": "app",
           "message": message, "meta": meta})
}

fn read_all(source: &Source) -> Vec<Value> {
    let mut records = Vec::new();
    source
        .for_each(None, &mut |record| {
            records.push(record);
            ControlFlow::Continue(())
        })
        .expect("a readable source");
    records
}

fn messages(records: &[Value]) -> Vec<&str> {
    records
        .iter()
        .map(|r| r["message"].as_str().unwrap())
        .collect()
}

fn query(text: &str, level: Option<&str>, run_id: Option<&str>, limit: usize) -> Query {
    Query {
        text: text.to_lowercase(),
        level: level.map(String::from),
        run_id: run_id.map(String::from),
        limit,
    }
}

/// One agent run in write order: a span closes after what is nested inside it.
fn a_run(run_id: &str) -> Vec<Value> {
    let meta = |extra: Value| {
        let mut meta = json!({"run_id": run_id});
        meta.as_object_mut()
            .unwrap()
            .extend(extra.as_object().unwrap().clone());
        meta
    };
    let mut llm = record("INFO", "chat", "2026-01-01T00:00:02.000Z", meta(json!({})));
    llm["llm"] = json!({"model": "m", "tokens_in": 10, "tokens_out": 5, "cost_usd": 0.02});
    vec![
        record("INFO", "plan", "2026-01-01T00:00:01.000Z", meta(json!({}))),
        llm,
        record("INFO", "step", "2026-01-01T00:00:03.000Z", meta(json!({}))),
        record("INFO", "call", "2026-01-01T00:00:04.000Z", meta(json!({}))),
        record(
            "INFO",
            "run",
            "2026-01-01T00:00:05.000Z",
            meta(json!({"operation": "invoke_agent", "agent_name": "planner"})),
        ),
        record(
            "ERROR",
            "something broke",
            "2026-01-01T00:00:06.000Z",
            meta(json!({})),
        ),
    ]
}

fn write_jsonl(path: &PathBuf, records: &[Value]) {
    let text: String = records.iter().map(|r| format!("{r}\n")).collect();
    fs::write(path, text).unwrap();
}

#[test]
fn a_jsonl_source_reads_every_record_in_file_order() {
    let path = scratch("jsonl-order").join("app.log");
    write_jsonl(&path, &a_run("run-1"));

    let records = read_all(&Source::Jsonl(path));

    assert_eq!(
        messages(&records),
        ["plan", "chat", "step", "call", "run", "something broke"]
    );
}

#[test]
fn a_jsonl_source_skips_malformed_and_non_object_lines() {
    let path = scratch("jsonl-malformed").join("app.log");
    fs::write(
        &path,
        "{\"message\": \"a\", \"meta\": {}}\nnot json\n[\"not\", \"an\", \"object\"]\n",
    )
    .unwrap();

    assert_eq!(messages(&read_all(&Source::Jsonl(path))), ["a"]);
}

fn sqlite_logs(path: &PathBuf) -> Connection {
    let connection = Connection::open(path).unwrap();
    connection
        .execute(
            "CREATE TABLE logs (id INTEGER PRIMARY KEY, timestamp TEXT, level TEXT, logger TEXT, \
             message TEXT, meta TEXT, run_id TEXT, span_id TEXT, parent_span_id TEXT, trace_id TEXT)",
            [],
        )
        .unwrap();
    connection
}

fn sqlite_source(path: PathBuf) -> Source {
    Source::Sqlite {
        path,
        table: "logs".into(),
    }
}

#[test]
fn a_sqlite_source_rebuilds_records_with_the_ids_taken_from_their_columns() {
    let path = scratch("sqlite-ids").join("logs.sqlite");
    let connection = sqlite_logs(&path);
    connection
        .execute(
            "INSERT INTO logs (timestamp, level, logger, message, meta, run_id, span_id) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![
                "t",
                "INFO",
                "app",
                "hello",
                "{\"user_id\": 42}",
                "run-1",
                "s1"
            ],
        )
        .unwrap();
    drop(connection);

    let records = read_all(&sqlite_source(path));

    assert_eq!(records.len(), 1);
    assert_eq!(records[0]["message"], "hello");
    assert_eq!(records[0]["meta"]["user_id"], 42);
    assert_eq!(records[0]["meta"]["run_id"], "run-1");
    assert_eq!(records[0]["meta"]["span_id"], "s1");
}

#[test]
fn a_sqlite_record_has_no_llm_block_because_the_table_has_no_such_column() {
    let path = scratch("sqlite-llm").join("logs.sqlite");
    let connection = sqlite_logs(&path);
    connection
        .execute(
            "INSERT INTO logs (timestamp, level, logger, message, meta, run_id) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params!["t", "INFO", "app", "chat", "{\"tokens_in\": 5, \"model\": \"m\"}", "run-1"],
        )
        .unwrap();
    drop(connection);

    let records = read_all(&sqlite_source(path));

    assert!(records[0].get("llm").is_none());
}

#[test]
fn a_null_meta_column_leaves_only_the_run_id() {
    let path = scratch("sqlite-null-meta").join("logs.sqlite");
    let connection = sqlite_logs(&path);
    connection
        .execute(
            "INSERT INTO logs (timestamp, level, logger, message, meta, run_id) VALUES (?1, ?2, ?3, ?4, NULL, ?5)",
            params!["t", "INFO", "app", "hi", "run-1"],
        )
        .unwrap();
    drop(connection);

    let records = read_all(&sqlite_source(path));

    assert_eq!(records[0]["meta"], json!({"run_id": "run-1"}));
}

#[test]
fn a_run_summary_adds_up_tokens_cost_levels_and_the_agent_name() {
    let path = scratch("summary").join("app.log");
    write_jsonl(&path, &a_run("run-1"));

    let summaries = summarise(&Source::Jsonl(path)).unwrap();

    assert_eq!(summaries.len(), 1);
    let run = &summaries[0];
    assert_eq!(run["run_id"], "run-1");
    assert_eq!(run["record_count"], 6);
    assert_eq!(run["tokens_in"], 10);
    assert_eq!(run["tokens_out"], 5);
    assert_eq!(run["cost_usd"], 0.02);
    assert_eq!(run["agent_name"], "planner");
    assert_eq!(run["level_counts"], json!({"INFO": 5, "ERROR": 1}));
    assert_eq!(run["error_count"], 1);
    assert_eq!(run["start"], "2026-01-01T00:00:01.000Z");
    assert_eq!(run["end"], "2026-01-01T00:00:06.000Z");
}

#[test]
fn records_without_a_run_id_make_no_summary() {
    let path = scratch("summary-loose").join("app.log");
    write_jsonl(&path, &[record("INFO", "loose", "t", json!({}))]);

    assert!(summarise(&Source::Jsonl(path)).unwrap().is_empty());
}

#[test]
fn run_summaries_are_listed_newest_start_first() {
    let path = scratch("summary-order").join("app.log");
    write_jsonl(
        &path,
        &[
            record(
                "INFO",
                "a",
                "2026-01-01T00:00:00.000Z",
                json!({"run_id": "old"}),
            ),
            record(
                "INFO",
                "b",
                "2026-01-02T00:00:00.000Z",
                json!({"run_id": "new"}),
            ),
        ],
    );

    let summaries = summarise(&Source::Jsonl(path)).unwrap();
    let ids: Vec<&str> = summaries
        .iter()
        .map(|s| s["run_id"].as_str().unwrap())
        .collect();

    assert_eq!(ids, ["new", "old"]);
}

fn searchable() -> Source {
    let path = scratch("search").join("app.log");
    write_jsonl(
        &path,
        &[
            record("ERROR", "search failed", "t", json!({"run_id": "a"})),
            record("INFO", "search ok", "t", json!({"run_id": "a"})),
            record("ERROR", "search failed", "t", json!({"run_id": "b"})),
            record("ERROR", "unrelated", "t", json!({"run_id": "a"})),
            record("INFO", "x", "t", json!({"user_id": "customer-42"})),
        ],
    );
    Source::Jsonl(path)
}

#[test]
fn search_filters_by_text_level_and_run() {
    let source = searchable();

    assert_eq!(
        search(&source, &query("search", None, None, 100))
            .unwrap()
            .len(),
        3
    );
    assert_eq!(
        search(&source, &query("search", Some("ERROR"), None, 100))
            .unwrap()
            .len(),
        2
    );
    assert_eq!(
        search(&source, &query("search", None, Some("b"), 100))
            .unwrap()
            .len(),
        1
    );
}

#[test]
fn search_matches_inside_meta_too() {
    let source = searchable();

    assert_eq!(
        search(&source, &query("customer-42", None, None, 100))
            .unwrap()
            .len(),
        1
    );
    assert!(search(&source, &query("nope", None, None, 100))
        .unwrap()
        .is_empty());
}

#[test]
fn search_stops_at_the_limit_and_with_no_filter_returns_records_in_order() {
    let source = searchable();

    assert_eq!(search(&source, &query("", None, None, 2)).unwrap().len(), 2);
    let all = search(&source, &query("", None, None, 100)).unwrap();
    assert_eq!(
        messages(&all),
        [
            "search failed",
            "search ok",
            "search failed",
            "unrelated",
            "x"
        ]
    );
}
