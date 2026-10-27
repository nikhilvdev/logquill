//! End-to-end tests of `tail`, `trace`, `dev` and `serve` against real files and a real socket.
//! The old Python CLI's tests were the checklist (`docs/spec/legacy-tests/python-tests.txt`).

use std::fs;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Output, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::thread;
use std::time::{Duration, Instant};

use logquill_cli::dev::frame;
use logquill_core::{create_table_sql, sql_row, Dialect};
use serde_json::{json, Value};

fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("logquill-commands-{}-{name}", std::process::id()));
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).expect("a scratch directory");
    dir
}

fn command(args: &[&str]) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_logquill"));
    command.args(args);
    command
}

fn run(args: &[&str]) -> Output {
    command(args).output().expect("run logquill")
}

fn out(output: &Output) -> String {
    String::from_utf8_lossy(&output.stdout).into_owned()
}

fn err(output: &Output) -> String {
    String::from_utf8_lossy(&output.stderr).into_owned()
}

fn record(level: &str, message: &str, meta: Value) -> Value {
    json!({
        "schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.000Z", "level": level,
        "logger": "app", "message": message, "meta": meta
    })
}

fn write_lines(path: &PathBuf, records: &[Value]) {
    let text: String = records.iter().map(|r| format!("{r}\n")).collect();
    fs::write(path, text).expect("write the log");
}

fn append(path: &PathBuf, text: &str) {
    let mut file = fs::OpenOptions::new().append(true).open(path).unwrap();
    file.write_all(text.as_bytes()).unwrap();
}

/// A run with a span: the child is written before the span closes, as the library does.
fn run_records(run: &str) -> Vec<Value> {
    let mut call = record(
        "INFO",
        "call_llm",
        json!({"run_id": run, "span_id": "s2", "parent_span_id": "s1", "duration_ms": 1200.5}),
    );
    call["llm"] = json!({"tokens_in": 10, "tokens_out": 5, "cost_usd": 0.002});
    vec![
        call,
        record(
            "INFO",
            "agent",
            json!({"run_id": run, "span_id": "s1", "kind": "span", "duration_ms": 2500}),
        ),
    ]
}

/// A running command whose stdout arrives line by line; killed when dropped.
struct Live {
    child: Child,
    lines: Receiver<String>,
}

impl Live {
    fn start(args: &[&str]) -> Live {
        let mut child = command(args)
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .expect("start logquill");
        let stdout = child.stdout.take().unwrap();
        let (tx, lines) = mpsc::channel();
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if tx.send(line).is_err() {
                    break;
                }
            }
        });
        Live { child, lines }
    }

    /// Output lines until one contains `needle`; panics after 15 seconds.
    fn until(&self, needle: &str) -> String {
        let mut seen = String::new();
        let deadline = Instant::now() + Duration::from_secs(15);
        while Instant::now() < deadline {
            if let Ok(line) = self.lines.recv_timeout(Duration::from_millis(200)) {
                seen.push_str(&line);
                seen.push('\n');
                if line.contains(needle) {
                    return seen;
                }
            }
        }
        panic!("never saw {needle:?}; got:\n{seen}");
    }
}

impl Drop for Live {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

// ---- tail ---------------------------------------------------------------------------------

fn mixed_log(name: &str) -> PathBuf {
    let path = scratch(name).join("app.log");
    write_lines(
        &path,
        &[
            record("DEBUG", "d1", json!({})),
            record("INFO", "i1", json!({})),
            record("WARN", "w1", json!({})),
            record("ERROR", "e1", json!({})),
            record("ERROR", "e2", json!({})),
        ],
    );
    path
}

#[test]
fn tail_prints_human_readable_lines_by_default_and_filters_by_level() {
    let path = mixed_log("tail-level");
    let all = out(&run(&["tail", path.to_str().unwrap()]));
    assert_eq!(all.lines().count(), 5);
    assert!(all.contains("INFO") && all.contains("i1"));
    let output = out(&run(&["tail", path.to_str().unwrap(), "--level", "warn"]));
    let messages: Vec<&str> = output.lines().collect();
    assert_eq!(messages.len(), 3);
    assert!(messages[0].contains("w1") && messages[2].contains("e2"));
}

#[test]
fn tail_json_prints_raw_json_lines() {
    let path = mixed_log("tail-json");
    let output = out(&run(&["tail", path.to_str().unwrap(), "--json", "-n", "1"]));
    let parsed: Value = serde_json::from_str(output.trim()).unwrap();
    assert_eq!(parsed["message"], "e2");
}

#[test]
fn tail_lines_limits_to_the_last_matching_records() {
    let path = mixed_log("tail-n");
    let output = out(&run(&[
        "tail",
        path.to_str().unwrap(),
        "--level",
        "ERROR",
        "-n",
        "1",
    ]));
    assert_eq!(output.lines().count(), 1);
    assert!(output.contains("e2"));
    let none = out(&run(&["tail", path.to_str().unwrap(), "-n", "0"]));
    assert!(none.is_empty());
}

#[test]
fn tail_missing_file_exits_nonzero_and_says_so() {
    let output = run(&["tail", "/no/such/dir/app.log"]);
    assert_eq!(output.status.code(), Some(1));
    assert!(err(&output).contains("logquill tail: no such file: /no/such/dir/app.log"));
}

#[test]
fn tail_skips_malformed_and_non_object_lines_with_a_warning() {
    let path = scratch("tail-bad").join("app.log");
    fs::write(
        &path,
        format!(
            "not json\n[1,2]\n\n{}\n\u{fffd}\u{fffd}\n",
            record("INFO", "fine", json!({}))
        ),
    )
    .unwrap();
    let output = run(&["tail", path.to_str().unwrap()]);
    assert_eq!(output.status.code(), Some(0));
    assert!(out(&output).contains("fine"));
    let stderr = err(&output);
    assert!(stderr.contains("logquill tail: skipping malformed JSON line: 'not json'"));
    assert!(stderr.contains("logquill tail: skipping non-object JSON line: '[1,2]'"));
}

#[test]
fn tail_unknown_level_is_a_usage_error() {
    let path = mixed_log("tail-usage");
    let output = run(&["tail", path.to_str().unwrap(), "--level", "loud"]);
    assert_eq!(output.status.code(), Some(2));
}

#[test]
fn tail_a_record_with_no_valid_level_is_dropped_only_when_filtering() {
    let path = scratch("tail-nolevel").join("app.log");
    write_lines(
        &path,
        &[json!({"message": "odd", "level": "LOUD", "meta": {}})],
    );
    assert!(out(&run(&["tail", path.to_str().unwrap()])).contains("odd"));
    assert!(out(&run(&["tail", path.to_str().unwrap(), "--level", "TRACE"])).is_empty());
}

#[test]
fn tail_follow_prints_appended_records_and_survives_rotation() {
    let path = scratch("tail-follow").join("app.log");
    write_lines(&path, &[record("INFO", "before", json!({}))]);
    let live = Live::start(&["tail", path.to_str().unwrap(), "-f", "--json"]);
    live.until("before");

    append(
        &path,
        &format!("{}\n", record("INFO", "appended", json!({}))),
    );
    live.until("appended");

    // A partial line is held until its newline arrives.
    let line = format!("{}\n", record("INFO", "split", json!({})));
    let (head, tail) = line.split_at(20);
    append(&path, head);
    thread::sleep(Duration::from_millis(1200));
    append(&path, tail);
    live.until("split");

    // Rotation: the file is renamed away and a new one takes its place.
    fs::rename(&path, path.with_extension("log.1")).unwrap();
    write_lines(&path, &[record("INFO", "after-rotation", json!({}))]);
    live.until("after-rotation");

    // Truncation in place.
    fs::write(&path, "").unwrap();
    thread::sleep(Duration::from_millis(700));
    append(
        &path,
        &format!("{}\n", record("INFO", "after-truncate", json!({}))),
    );
    live.until("after-truncate");
}

// ---- trace --------------------------------------------------------------------------------

#[test]
fn trace_prints_the_annotated_tree_and_ignores_other_runs() {
    let path = scratch("trace-text").join("app.log");
    let mut records = run_records("r1");
    records.extend(run_records("r2"));
    records.insert(1, record("INFO", "other run", json!({"run_id": "r9"})));
    write_lines(&path, &records);
    let output = run(&["trace", "r1", "--file", path.to_str().unwrap()]);
    assert_eq!(output.status.code(), Some(0));
    assert_eq!(
        out(&output),
        "\u{2514}\u{2500} [INFO] agent  (2,500.0ms, 10\u{2192}5 tok, $0.0020)\n   \u{2514}\u{2500} [INFO] call_llm  (1,200.5ms, 10\u{2192}5 tok, $0.0020)\n"
    );
}

#[test]
fn trace_json_is_one_array_of_nested_nodes() {
    let path = scratch("trace-json").join("app.log");
    write_lines(&path, &run_records("r1"));
    let output = run(&["trace", "r1", "--file", path.to_str().unwrap(), "--json"]);
    let roots: Value = serde_json::from_str(out(&output).trim()).unwrap();
    assert_eq!(roots.as_array().unwrap().len(), 1);
    let root = &roots[0];
    assert_eq!(root["record"]["message"], "agent");
    assert_eq!(root["rollup"]["tokens_in"], 10);
    assert_eq!(root["children"][0]["record"]["message"], "call_llm");
}

#[test]
fn trace_errors_name_the_problem_and_exit_nonzero() {
    let path = scratch("trace-errors").join("app.log");
    write_lines(&path, &run_records("r1"));
    let unknown = run(&["trace", "nope", "--file", path.to_str().unwrap()]);
    assert_eq!(unknown.status.code(), Some(1));
    assert!(err(&unknown).contains("no records with meta.run_id='nope' found in"));
    let missing = run(&["trace", "r1", "--file", "/no/such/app.log"]);
    assert_eq!(missing.status.code(), Some(1));
    assert!(err(&missing).contains("logquill trace: no such file: /no/such/app.log"));
    assert_eq!(run(&["trace", "r1"]).status.code(), Some(2));
}

#[test]
fn trace_skips_malformed_lines_with_a_warning_but_still_traces() {
    let path = scratch("trace-malformed").join("app.log");
    let mut text = String::from("{broken\n");
    for r in run_records("r1") {
        text.push_str(&format!("{r}\n"));
    }
    fs::write(&path, text).unwrap();
    let output = run(&["trace", "r1", "--file", path.to_str().unwrap()]);
    assert_eq!(output.status.code(), Some(0));
    assert!(out(&output).contains("agent"));
    assert!(err(&output).contains("logquill trace: skipping malformed JSON line"));
}

// ---- dev ----------------------------------------------------------------------------------

#[test]
fn dev_draws_the_latest_run_and_redraws_when_records_arrive() {
    let path = scratch("dev-latest").join("app.log");
    let mut records = run_records("r1");
    records.extend(run_records("r2"));
    write_lines(&path, &records);
    let live = Live::start(&["dev", path.to_str().unwrap(), "--no-color"]);
    let first = live.until("call_llm");
    assert!(first.contains("logquill dev \u{2014} run r2"));
    assert!(
        !first.contains('\u{1b}'),
        "no escape codes without a terminal"
    );

    append(
        &path,
        &format!(
            "{}\n",
            record("INFO", "later step", json!({"run_id": "r2"}))
        ),
    );
    live.until("later step");

    // A newer run takes over once it starts.
    append(
        &path,
        &format!(
            "{}\n",
            record("INFO", "fresh start", json!({"run_id": "r3"}))
        ),
    );
    let switched = live.until("fresh start");
    assert!(switched.contains("run r3"));
}

#[test]
fn dev_never_switches_away_from_an_explicit_run() {
    let path = scratch("dev-explicit").join("app.log");
    let mut records = run_records("r1");
    records.extend(run_records("r2"));
    write_lines(&path, &records);
    let live = Live::start(&[
        "dev",
        path.to_str().unwrap(),
        "--run-id",
        "r1",
        "--no-color",
    ]);
    assert!(live.until("call_llm").contains("run r1"));
    append(
        &path,
        &format!("{}\n", record("INFO", "elsewhere", json!({"run_id": "r2"}))),
    );
    append(
        &path,
        &format!("{}\n", record("INFO", "mine", json!({"run_id": "r1"}))),
    );
    let after = live.until("mine");
    assert!(after.contains("run r1") && !after.contains("elsewhere"));
}

#[test]
fn dev_with_no_run_yet_draws_nothing_and_a_missing_file_is_an_error() {
    let path = scratch("dev-empty").join("app.log");
    write_lines(&path, &[record("INFO", "no run id", json!({}))]);
    let live = Live::start(&["dev", path.to_str().unwrap()]);
    assert!(live
        .lines
        .recv_timeout(Duration::from_millis(1500))
        .is_err());
    let missing = run(&["dev", "/no/such/app.log"]);
    assert_eq!(missing.status.code(), Some(1));
    assert!(err(&missing).contains("logquill dev: no such file: /no/such/app.log"));
}

#[test]
fn dev_backlog_caps_how_much_history_seeds_the_view() {
    let path = scratch("dev-backlog").join("app.log");
    write_lines(&path, &run_records("r1"));
    // Only the last line (the span's own record) is seeded; its child is out of the window.
    let live = Live::start(&[
        "dev",
        path.to_str().unwrap(),
        "--backlog",
        "1",
        "--no-color",
    ]);
    let screen = live.until("agent");
    assert!(!screen.contains("call_llm"));
}

#[test]
fn a_dev_frame_clears_and_colours_only_when_asked() {
    let records = run_records("r1");
    let plain = frame(&records, "r1", false);
    assert!(!plain.contains('\u{1b}'));
    let coloured = frame(&records, "r1", true);
    assert!(coloured.starts_with("\u{1b}[2J\u{1b}[H"));
    assert!(coloured.contains("\u{1b}[32m"), "INFO is green");
}

// ---- serve --------------------------------------------------------------------------------

fn get(url: &str, host_header: Option<&str>) -> (u16, String, String) {
    let rest = url.strip_prefix("http://").unwrap();
    let (authority, path) = rest.split_once('/').unwrap();
    let mut stream = TcpStream::connect(authority).unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(10)))
        .unwrap();
    let host = host_header.unwrap_or(authority);
    write!(
        stream,
        "GET /{path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
    )
    .unwrap();
    let mut response = String::new();
    stream.read_to_string(&mut response).unwrap();
    let (head, body) = response.split_once("\r\n\r\n").unwrap();
    let status = head.split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, head.to_string(), body.to_string())
}

/// Starts `serve` and returns it with its base URL.
fn serving(args: &[&str]) -> (Live, String) {
    let live = Live::start(args);
    let banner = live.until("listening on");
    let url = banner
        .split("listening on ")
        .nth(1)
        .and_then(|s| s.split_whitespace().next())
        .unwrap()
        .to_string();
    (live, url)
}

#[test]
fn serve_needs_exactly_one_source_and_a_real_file() {
    assert_eq!(run(&["serve"]).status.code(), Some(2));
    let both = run(&["serve", "--file", "a", "--db", "b"]);
    assert_eq!(both.status.code(), Some(2));
    let missing = run(&["serve", "--file", "/no/such/app.log"]);
    assert_eq!(missing.status.code(), Some(1));
    assert!(err(&missing).contains("logquill serve: no such file: /no/such/app.log"));
    let bad_table = run(&[
        "serve",
        "--db",
        "/no/such.db",
        "--table",
        "logs; DROP TABLE logs",
    ]);
    assert_eq!(bad_table.status.code(), Some(1));
}

#[test]
fn serve_answers_the_viewer_page_and_the_api_from_a_jsonl_file() {
    let path = scratch("serve-file").join("app.log");
    let mut records = run_records("r1");
    records.push(record("ERROR", "boom", json!({"run_id": "r1"})));
    records.extend(run_records("r2"));
    records.push(record("INFO", "no run here needle", json!({})));
    write_lines(&path, &records);
    let (_live, url) = serving(&["serve", "--file", path.to_str().unwrap()]);

    let (status, head, body) = get(&url, None);
    assert_eq!(status, 200);
    assert!(head.contains("text/html; charset=utf-8"));
    assert!(body.contains("local trace viewer"));

    let (_, _, runs) = get(&format!("{url}api/runs"), None);
    let runs: Value = serde_json::from_str(&runs).unwrap();
    assert_eq!(runs.as_array().unwrap().len(), 2);
    let r1 = runs
        .as_array()
        .unwrap()
        .iter()
        .find(|r| r["run_id"] == "r1")
        .unwrap();
    assert_eq!(r1["record_count"], 3);
    assert_eq!(r1["error_count"], 1);
    assert_eq!(r1["tokens_in"], 10);
    assert_eq!(r1["level_counts"]["ERROR"], 1);

    let (status, _, tree) = get(&format!("{url}api/trace?run_id=r1"), None);
    assert_eq!(status, 200);
    let tree: Value = serde_json::from_str(&tree).unwrap();
    let agent = tree
        .as_array()
        .unwrap()
        .iter()
        .find(|n| n["record"]["message"] == "agent")
        .unwrap();
    assert_eq!(agent["children"][0]["record"]["message"], "call_llm");
    assert_eq!(get(&format!("{url}api/trace"), None).0, 400);

    let (_, _, hits) = get(&format!("{url}api/search?q=NEEDLE"), None);
    let hits: Value = serde_json::from_str(&hits).unwrap();
    assert_eq!(
        hits.as_array().unwrap().len(),
        1,
        "records without a run id are searchable"
    );
    let (_, _, errors) = get(&format!("{url}api/search?level=ERROR&run_id=r1"), None);
    assert_eq!(
        serde_json::from_str::<Value>(&errors).unwrap()[0]["message"],
        "boom"
    );

    assert_eq!(get(&format!("{url}api/search?limit=abc"), None).0, 400);
    let (_, _, one) = get(&format!("{url}api/search?limit=0"), None);
    assert_eq!(
        serde_json::from_str::<Value>(&one)
            .unwrap()
            .as_array()
            .unwrap()
            .len(),
        1
    );
    assert_eq!(get(&format!("{url}nope"), None).0, 404);
}

#[test]
fn serve_refuses_requests_addressed_to_another_host_name() {
    let path = scratch("serve-rebind").join("app.log");
    write_lines(&path, &run_records("r1"));
    let (_live, url) = serving(&["serve", "--file", path.to_str().unwrap()]);
    assert_eq!(get(&url, Some("evil.example.com")).0, 403);
    assert_eq!(get(&url, Some("localhost:1234")).0, 200);
}

#[test]
fn serve_reads_the_sqlite_transports_table() {
    let dir = scratch("serve-db");
    let db = dir.join("logs.db");
    let connection = rusqlite::Connection::open(&db).unwrap();
    connection
        .execute_batch(&create_table_sql(Dialect::Sqlite, "logs"))
        .unwrap();
    for r in run_records("r7") {
        let row = sql_row(&r);
        let columns = [
            "timestamp",
            "level",
            "logger",
            "message",
            "meta",
            "run_id",
            "span_id",
            "parent_span_id",
        ];
        let values: Vec<String> = columns
            .iter()
            .map(|c| row[*c].as_str().unwrap_or("").to_string())
            .collect();
        connection
            .execute(
                &format!(
                    "INSERT INTO logs ({}) VALUES (?1,?2,?3,?4,?5,?6,?7,?8)",
                    columns.join(",")
                ),
                rusqlite::params_from_iter(values),
            )
            .unwrap();
    }
    drop(connection);
    let (_live, url) = serving(&["serve", "--db", db.to_str().unwrap()]);
    let (_, _, runs) = get(&format!("{url}api/runs"), None);
    let runs: Value = serde_json::from_str(&runs).unwrap();
    assert_eq!(runs[0]["run_id"], "r7");
    assert_eq!(runs[0]["tokens_in"], 0, "the table carries no llm block");
    let (_, _, tree) = get(&format!("{url}api/trace?run_id=r7"), None);
    let tree: Value = serde_json::from_str(&tree).unwrap();
    let agent = tree
        .as_array()
        .unwrap()
        .iter()
        .find(|n| n["record"]["message"] == "agent")
        .unwrap();
    assert_eq!(agent["children"][0]["record"]["message"], "call_llm");
}

#[test]
fn tail_prints_a_stack_trace_on_its_own_lines() {
    let path = scratch("tail-stack").join("app.log");
    let stack = "Traceback (most recent call last):\n  File \"app.py\", line 1\nValueError: boom";
    write_lines(&path, &[record("ERROR", "failed", json!({"stack": stack}))]);
    let output = out(&run(&["tail", path.to_str().unwrap()]));
    let lines: Vec<&str> = output.lines().collect();
    assert!(lines[0].contains("failed"));
    assert_eq!(&lines[1..], stack.lines().collect::<Vec<_>>());
}
