//! Syslog over real sockets, SQLite read back from disk, and the OTLP JSON shape.

use std::io::{BufRead, BufReader};
use std::net::{TcpListener, UdpSocket};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use logquill_runtime::{
    log_record_json, otlp_log_transport, otlp_trace_transport, span_json, sqlite_transport,
    HttpPost, HttpResponse, OtlpOptions, SqliteOptions, SyslogOptions, SyslogProtocol,
    SyslogTransport, Transport,
};
use serde_json::{json, Value};

fn record(level: &str, message: &str, meta: Value) -> Value {
    json!({"schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.123Z", "level": level,
           "logger": "svc.api", "message": message, "meta": meta})
}

fn write(transport: &mut dyn Transport, record: &Value) -> Result<(), String> {
    let line = transport.format(record);
    transport.write(&line, record)
}

#[test]
fn syslog_udp_sends_one_rfc5424_datagram_per_record() {
    let socket = UdpSocket::bind("127.0.0.1:0").unwrap();
    socket
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let options = SyslogOptions {
        host: "127.0.0.1".into(),
        port: socket.local_addr().unwrap().port(),
        hostname: Some("box1".into()),
        ..SyslogOptions::default()
    };
    let mut transport = SyslogTransport::new(options);
    write(&mut transport, &record("ERROR", "boom", json!({}))).unwrap();
    let mut buffer = [0u8; 2048];
    let n = socket.recv(&mut buffer).unwrap();
    let message = String::from_utf8_lossy(&buffer[..n]).to_string();
    // facility 1 * 8 + severity 3 (error) = 11
    assert!(
        message.starts_with("<11>1 2026-01-01T00:00:00.123Z box1 svc.api "),
        "{message}"
    );
    assert!(
        message.contains(&format!(" {} - - {{", std::process::id())),
        "{message}"
    );
    assert!(
        message.ends_with("\"message\":\"boom\",\"meta\":{}}"),
        "{message}"
    );
}

#[test]
fn syslog_tcp_frames_with_newlines_and_a_chosen_app_name() {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let options = SyslogOptions {
        host: "127.0.0.1".into(),
        port: listener.local_addr().unwrap().port(),
        protocol: SyslogProtocol::Tcp,
        app_name: Some("myapp".into()),
        hostname: Some("box1".into()),
        facility: 16,
        ..SyslogOptions::default()
    };
    let mut transport = SyslogTransport::new(options);
    write(&mut transport, &record("INFO", "one", json!({}))).unwrap();
    write(&mut transport, &record("FATAL", "two", json!({}))).unwrap();
    let (stream, _) = listener.accept().unwrap();
    stream
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let mut reader = BufReader::new(stream);
    let (mut first, mut second) = (String::new(), String::new());
    reader.read_line(&mut first).unwrap();
    reader.read_line(&mut second).unwrap();
    assert!(
        first.starts_with("<134>1 ") && first.contains(" myapp "),
        "{first}"
    ); // 16*8 + 6
    assert!(second.starts_with("<130>1 "), "{second}"); // 16*8 + 2
}

#[test]
fn syslog_rejects_an_unknown_protocol_and_reports_an_unreachable_tcp_server() {
    assert_eq!(
        SyslogProtocol::parse("sctp").unwrap_err(),
        "SyslogTransport: protocol must be 'udp' or 'tcp', got sctp"
    );
    let options = SyslogOptions {
        host: "127.0.0.1".into(),
        port: 9,
        protocol: SyslogProtocol::Tcp,
        ..SyslogOptions::default()
    };
    let mut transport = SyslogTransport::new(options);
    assert!(write(&mut transport, &record("INFO", "x", json!({}))).is_err());
}

fn scratch(name: &str) -> std::path::PathBuf {
    let dir = std::env::temp_dir().join(format!("logquill-sinks-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

#[test]
fn sqlite_stores_batched_rows_including_the_llm_block() {
    let path = scratch("db").join("logs.db");
    let options = SqliteOptions {
        filename: path.to_string_lossy().to_string(),
        ensure_schema: true,
        max_records: 3,
        ..SqliteOptions::default()
    };
    let mut transport = sqlite_transport(options).unwrap();
    write(
        &mut transport,
        &record(
            "INFO",
            "a",
            json!({"run_id": "r1", "span_id": "s1", "n": 1.0}),
        ),
    )
    .unwrap();
    let mut with_llm = record("INFO", "b", json!({"trace_id": "t1"}));
    with_llm["llm"] = json!({"model": "m", "tokens_in": 3});
    write(&mut transport, &with_llm).unwrap();
    write(
        &mut transport,
        &record("WARN", "c", json!({"caf\u{e9}": "\u{2603}"})),
    )
    .unwrap();
    transport.close().unwrap();

    let db = rusqlite::Connection::open(&path).unwrap();
    let count: i64 = db
        .query_row("SELECT COUNT(*) FROM logs", [], |r| r.get(0))
        .unwrap();
    assert_eq!(count, 3);
    let (message, meta, run_id, llm): (String, String, Option<String>, Option<String>) = db
        .query_row(
            "SELECT message, meta, run_id, llm FROM logs WHERE message = 'a'",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?)),
        )
        .unwrap();
    assert_eq!(
        (message.as_str(), run_id.as_deref(), llm),
        ("a", Some("r1"), None)
    );
    assert_eq!(meta, r#"{"run_id":"r1","span_id":"s1","n":1.0}"#);
    let llm: String = db
        .query_row("SELECT llm FROM logs WHERE message = 'b'", [], |r| r.get(0))
        .unwrap();
    assert_eq!(llm, r#"{"model":"m","tokens_in":3}"#);
    let trace: String = db
        .query_row("SELECT trace_id FROM logs WHERE message = 'b'", [], |r| {
            r.get(0)
        })
        .unwrap();
    assert_eq!(trace, "t1");
}

#[test]
fn sqlite_refuses_a_table_name_that_is_not_an_identifier() {
    for bad in ["logs; DROP TABLE x", "1logs", "a.b.c", "", "with space"] {
        let options = SqliteOptions {
            table_name: bad.into(),
            ..SqliteOptions::default()
        };
        let error = sqlite_transport(options).err().unwrap();
        assert!(
            error.contains("is not a plain SQL identifier"),
            "{bad}: {error}"
        );
    }
    let ok = SqliteOptions {
        table_name: "main.logs".into(),
        ..SqliteOptions::default()
    };
    assert!(sqlite_transport(ok).is_ok());
}

type Post = (String, Vec<(String, String)>, Vec<u8>);

#[derive(Clone, Default)]
struct Recording(Arc<Mutex<Vec<Post>>>);

impl HttpPost for Recording {
    fn post(
        &mut self,
        url: &str,
        headers: &[(String, String)],
        body: &[u8],
        _: Duration,
    ) -> Result<HttpResponse, String> {
        self.0
            .lock()
            .unwrap()
            .push((url.into(), headers.to_vec(), body.to_vec()));
        Ok(HttpResponse {
            status: 200,
            retry_after: None,
        })
    }
}

#[test]
fn an_llm_call_becomes_a_client_span_with_genai_attributes() {
    let mut call = record(
        "INFO",
        "llm_call",
        json!({"run_id": "run-1", "span_id": "00f067aa0ba902b7", "parent_span_id": "parent-1", "provider": "openai"}),
    );
    call["llm"] = json!({"model": "gpt-4o", "tokens_in": 10, "tokens_out": 20, "latency_ms": 250.0, "finish_reason": "stop"});
    let span = span_json(&call, &OtlpOptions::traces()).unwrap();
    assert_eq!(span["name"], "chat gpt-4o");
    assert_eq!(span["kind"], 3);
    assert_eq!(span["spanId"], "00f067aa0ba902b7");
    assert_eq!(span["traceId"].as_str().unwrap().len(), 32);
    assert_eq!(span["parentSpanId"].as_str().unwrap().len(), 16);
    let end: i128 = span["endTimeUnixNano"].as_str().unwrap().parse().unwrap();
    let start: i128 = span["startTimeUnixNano"].as_str().unwrap().parse().unwrap();
    assert_eq!(end, 1_767_225_600_123_000_000);
    assert_eq!(end - start, 250_000_000);
    let attrs = span["attributes"].as_array().unwrap();
    let find = |key: &str| {
        attrs
            .iter()
            .find(|a| a["key"] == key)
            .map(|a| a["value"].clone())
    };
    assert_eq!(
        find("gen_ai.usage.input_tokens"),
        Some(json!({"intValue": "10"}))
    );
    assert_eq!(
        find("gen_ai.provider.name"),
        Some(json!({"stringValue": "openai"}))
    );
    assert_eq!(
        find("gen_ai.response.finish_reasons"),
        Some(json!({"arrayValue": {"values": [{"stringValue": "stop"}]}}))
    );
    assert!(span.get("status").is_none());
}

#[test]
fn errors_set_the_span_status_and_plain_records_are_not_spans() {
    let mut failed = record(
        "ERROR",
        "tool",
        json!({"kind": "action", "tool": "search", "error": "Timeout: slow"}),
    );
    failed["meta"]["duration_ms"] = json!(5);
    let span = span_json(&failed, &OtlpOptions::traces()).unwrap();
    assert_eq!(span["name"], "execute_tool search");
    assert_eq!(
        span["status"],
        json!({"code": 2, "message": "Timeout: slow"})
    );
    assert!(span_json(
        &record("INFO", "just a line", json!({})),
        &OtlpOptions::traces()
    )
    .is_none());
}

#[test]
fn spans_are_posted_as_one_resource_spans_document() {
    let poster = Recording::default();
    let options = OtlpOptions {
        max_records: 2,
        headers: vec![("x-token".into(), "t".into())],
        ..OtlpOptions::traces()
    };
    let mut transport = otlp_trace_transport(options, Box::new(poster.clone()));
    write(&mut transport, &record("INFO", "plain line", json!({}))).unwrap();
    write(
        &mut transport,
        &record(
            "INFO",
            "work",
            json!({"kind": "span", "span_id": "s1", "run_id": "r"}),
        ),
    )
    .unwrap();
    let posts = poster.0.lock().unwrap();
    assert_eq!(posts.len(), 1);
    assert_eq!(posts[0].0, "http://localhost:4318/v1/traces");
    assert!(posts[0]
        .1
        .contains(&("x-token".to_string(), "t".to_string())));
    let body: Value = serde_json::from_slice(&posts[0].2).unwrap();
    let spans = &body["resourceSpans"][0]["scopeSpans"][0]["spans"];
    assert_eq!(
        spans.as_array().unwrap().len(),
        1,
        "the plain line is not a span"
    );
    assert_eq!(
        body["resourceSpans"][0]["resource"]["attributes"][0]["value"]["stringValue"],
        "logquill"
    );
}

#[test]
fn every_record_becomes_an_otlp_log_with_severity_and_trace_ids() {
    let mut line = record(
        "WARN",
        "careful",
        json!({"span_id": "s1", "run_id": "run-1", "n": 7, "ratio": 0.5, "tags": ["a", "b"], "mixed": [1, "x"], "stack": "tb", "gone": null}),
    );
    line["llm"] = json!({"model": "m", "tokens_in": 1});
    let log = log_record_json(&line, &OtlpOptions::logs());
    assert_eq!(log["severityNumber"], 13);
    assert_eq!(log["severityText"], "WARN");
    assert_eq!(log["body"], json!({"stringValue": "careful"}));
    assert_eq!(log["timeUnixNano"], "1767225600123000000");
    assert_eq!(log["traceId"].as_str().unwrap().len(), 32);
    assert_eq!(log["spanId"].as_str().unwrap().len(), 16);
    let attrs = log["attributes"].as_array().unwrap();
    let find = |key: &str| {
        attrs
            .iter()
            .find(|a| a["key"] == key)
            .map(|a| a["value"].clone())
    };
    assert_eq!(find("n"), Some(json!({"intValue": "7"})));
    assert_eq!(find("ratio"), Some(json!({"doubleValue": 0.5})));
    assert_eq!(
        find("tags"),
        Some(json!({"arrayValue": {"values": [{"stringValue": "a"}, {"stringValue": "b"}]}}))
    );
    assert_eq!(
        find("mixed"),
        Some(json!({"stringValue": "[1,\"x\"]"})),
        "mixed lists become JSON text"
    );
    assert_eq!(
        find("exception.stacktrace"),
        Some(json!({"stringValue": "tb"}))
    );
    assert!(find("stack").is_none() && find("gone").is_none());
    assert_eq!(
        find("logquill.logger"),
        Some(json!({"stringValue": "svc.api"}))
    );
    assert_eq!(
        find("gen_ai.operation.name"),
        Some(json!({"stringValue": "chat"}))
    );
    assert!(
        log_record_json(&record("INFO", "no ids", json!({})), &OtlpOptions::logs())
            .get("traceId")
            .is_none()
    );

    let poster = Recording::default();
    let mut transport = otlp_log_transport(
        OtlpOptions {
            max_records: 1,
            ..OtlpOptions::logs()
        },
        Box::new(poster.clone()),
    );
    write(&mut transport, &line).unwrap();
    let posts = poster.0.lock().unwrap();
    let body: Value = serde_json::from_slice(&posts[0].2).unwrap();
    assert_eq!(
        body["resourceLogs"][0]["scopeLogs"][0]["logRecords"]
            .as_array()
            .unwrap()
            .len(),
        1
    );
}
