//! The HTTP transports, against a real local server and (for the https-only sinks) a recording client.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use logquill_runtime::{
    app_insights_transport, datadog_transport, datadog_url, elasticsearch_transport,
    http_transport, new_relic_transport, retry_after_seconds, HttpOptions, HttpPost, HttpResponse,
    Transport, UreqPost,
};
use serde_json::{json, Value};

#[derive(Clone, Debug)]
struct Request {
    path: String,
    headers: Vec<(String, String)>,
    body: Vec<u8>,
}

impl Request {
    fn header(&self, name: &str) -> Option<&str> {
        self.headers
            .iter()
            .find(|(k, _)| k.eq_ignore_ascii_case(name))
            .map(|(_, v)| v.as_str())
    }
}

/// A one-thread HTTP/1.1 server that answers every request with a fixed status and records it.
struct Server {
    url: String,
    requests: Arc<Mutex<Vec<Request>>>,
}

fn read_request(stream: &mut TcpStream) -> Option<Request> {
    let mut data = Vec::new();
    let mut chunk = [0u8; 4096];
    let (head_end, length) = loop {
        let n = stream.read(&mut chunk).ok()?;
        if n == 0 {
            return None;
        }
        data.extend_from_slice(&chunk[..n]);
        if let Some(at) = data.windows(4).position(|w| w == b"\r\n\r\n") {
            let head = String::from_utf8_lossy(&data[..at]).to_string();
            let length = head
                .lines()
                .find_map(|l| {
                    l.to_ascii_lowercase()
                        .strip_prefix("content-length:")
                        .map(|v| v.trim().parse::<usize>().ok())
                })
                .flatten()
                .unwrap_or(0);
            break (at + 4, length);
        }
    };
    while data.len() < head_end + length {
        let n = stream.read(&mut chunk).ok()?;
        if n == 0 {
            break;
        }
        data.extend_from_slice(&chunk[..n]);
    }
    let head = String::from_utf8_lossy(&data[..head_end]).to_string();
    let mut lines = head.lines();
    let path = lines.next()?.split_whitespace().nth(1)?.to_string();
    let headers = lines
        .filter_map(|l| {
            l.split_once(':')
                .map(|(k, v)| (k.trim().to_string(), v.trim().to_string()))
        })
        .collect();
    Some(Request {
        path,
        headers,
        body: data[head_end..head_end + length.min(data.len() - head_end)].to_vec(),
    })
}

fn serve(status: u16) -> Server {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let requests = Arc::new(Mutex::new(Vec::new()));
    let log = requests.clone();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { break };
            if let Some(request) = read_request(&mut stream) {
                log.lock().unwrap().push(request);
            }
            let reply =
                format!("HTTP/1.1 {status} X\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
            let _ = stream.write_all(reply.as_bytes());
        }
    });
    Server { url, requests }
}

fn record(level: &str, message: &str) -> Value {
    json!({"schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.000Z", "level": level,
           "logger": "svc", "message": message, "meta": {"user": "u1", "eventType": "x", "stack": "tb"}})
}

fn write(transport: &mut dyn Transport, record: &Value) -> Result<(), String> {
    let line = transport.format(record);
    transport.write(&line, record)
}

fn options(max_records: usize) -> HttpOptions {
    HttpOptions {
        max_records,
        timeout: Duration::from_secs(5),
        ..HttpOptions::default()
    }
}

#[test]
fn ndjson_batches_are_one_post_of_newline_joined_lines() {
    let server = serve(200);
    let mut transport = http_transport(
        &format!("{}/logs", server.url),
        options(2),
        Box::new(UreqPost),
    );
    write(&mut transport, &record("INFO", "a")).unwrap();
    assert!(
        server.requests.lock().unwrap().is_empty(),
        "nothing is sent before the batch is full"
    );
    write(&mut transport, &record("INFO", "b")).unwrap();
    let requests = server.requests.lock().unwrap();
    assert_eq!(requests.len(), 1);
    assert_eq!(requests[0].path, "/logs");
    assert_eq!(
        requests[0].header("content-type"),
        Some("application/x-ndjson")
    );
    let body = String::from_utf8(requests[0].body.clone()).unwrap();
    assert_eq!(body.lines().count(), 2);
    assert!(body
        .lines()
        .all(|l| serde_json::from_str::<Value>(l).is_ok()));
}

#[test]
fn a_server_error_or_an_unreachable_host_drops_the_batch_and_says_why() {
    let server = serve(500);
    let mut transport = http_transport(
        &format!("{}/logs", server.url),
        options(1),
        Box::new(UreqPost),
    );
    let error = write(&mut transport, &record("INFO", "a")).unwrap_err();
    assert!(
        error.contains("couldn't deliver 1 log record(s)") && error.contains("HTTP 500"),
        "{error}"
    );
    assert_eq!(transport.pending(), 0);

    let mut gone = http_transport("http://127.0.0.1:9/logs", options(1), Box::new(UreqPost));
    let error = write(&mut gone, &record("INFO", "a")).unwrap_err();
    assert!(
        error.contains("check the URL is reachable and accepts POSTed NDJSON"),
        "{error}"
    );
}

#[test]
fn elasticsearch_posts_bulk_actions_and_ecs_documents() {
    let server = serve(200);
    let mut transport = elasticsearch_transport(
        &format!("{}/", server.url),
        "logs-app",
        options(2),
        Box::new(UreqPost),
    );
    write(&mut transport, &record("ERROR", "boom")).unwrap();
    write(&mut transport, &record("INFO", "ok")).unwrap();
    let requests = server.requests.lock().unwrap();
    assert_eq!(requests[0].path, "/_bulk", "trailing slashes are trimmed");
    let body = String::from_utf8(requests[0].body.clone()).unwrap();
    assert!(body.ends_with('\n'));
    let lines: Vec<Value> = body
        .lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect();
    assert_eq!(lines.len(), 4);
    assert_eq!(lines[0], json!({"index": {"_index": "logs-app"}}));
    assert_eq!(lines[1]["@timestamp"], "2026-01-01T00:00:00.000Z");
    assert_eq!(lines[1]["log"]["level"], "error");
    assert_eq!(lines[1]["error"]["stack_trace"], "tb");
}

type Post = (String, Vec<(String, String)>, Vec<u8>);

/// Records what it was asked to post and answers from a script.
#[derive(Clone, Default)]
struct Recording {
    posts: Arc<Mutex<Vec<Post>>>,
    answers: Arc<Mutex<Vec<HttpResponse>>>,
}

impl HttpPost for Recording {
    fn post(
        &mut self,
        url: &str,
        headers: &[(String, String)],
        body: &[u8],
        _: Duration,
    ) -> Result<HttpResponse, String> {
        self.posts
            .lock()
            .unwrap()
            .push((url.to_string(), headers.to_vec(), body.to_vec()));
        let mut answers = self.answers.lock().unwrap();
        Ok(if answers.is_empty() {
            HttpResponse {
                status: 200,
                retry_after: None,
            }
        } else {
            answers.remove(0)
        })
    }
}

#[test]
fn datadog_posts_a_json_array_with_the_api_key() {
    let poster = Recording::default();
    let mut transport =
        datadog_transport("KEY", "datadoghq.eu", options(2), Box::new(poster.clone()));
    write(&mut transport, &record("INFO", "a")).unwrap();
    write(&mut transport, &record("INFO", "b")).unwrap();
    let posts = poster.posts.lock().unwrap().clone();
    assert_eq!(
        posts[0].0,
        "https://http-intake.logs.datadoghq.eu/api/v2/logs"
    );
    assert_eq!(
        datadog_url("datadoghq.com"),
        "https://http-intake.logs.datadoghq.com/api/v2/logs"
    );
    assert!(posts[0]
        .1
        .contains(&("DD-API-KEY".to_string(), "KEY".to_string())));
    let body: Value = serde_json::from_slice(&posts[0].2).unwrap();
    assert_eq!(body.as_array().unwrap().len(), 2);

    poster.answers.lock().unwrap().push(HttpResponse {
        status: 403,
        retry_after: None,
    });
    write(&mut transport, &record("INFO", "c")).unwrap();
    let error = write(&mut transport, &record("INFO", "d")).unwrap_err();
    assert!(
        error.contains("failed with status 403")
            && error.contains("check the API key and site region"),
        "{error}"
    );
}

#[test]
fn new_relic_sends_gzip_without_event_type_and_honours_a_429_pause() {
    let poster = Recording::default();
    let now = Arc::new(Mutex::new(1000.0_f64));
    let clock = {
        let now = now.clone();
        Arc::new(move || *now.lock().unwrap())
    };
    let mut transport =
        new_relic_transport("LIC", "EU", options(1), Box::new(poster.clone()), clock).unwrap();
    write(&mut transport, &record("INFO", "first")).unwrap();
    {
        let posts = poster.posts.lock().unwrap();
        assert_eq!(posts[0].0, "https://log-api.eu.newrelic.com/log/v1");
        assert!(posts[0]
            .1
            .contains(&("Content-Encoding".to_string(), "gzip".to_string())));
        assert!(posts[0]
            .1
            .contains(&("Api-Key".to_string(), "LIC".to_string())));
        let mut text = String::new();
        flate2::read::GzDecoder::new(&posts[0].2[..])
            .read_to_string(&mut text)
            .unwrap();
        let body: Value = serde_json::from_str(&text).unwrap();
        assert!(body[0]["meta"].get("eventType").is_none() && body[0]["meta"]["user"] == "u1");
    }

    poster.answers.lock().unwrap().push(HttpResponse {
        status: 429,
        retry_after: Some("120".into()),
    });
    assert!(write(&mut transport, &record("INFO", "limited")).is_err());
    let sent_before = poster.posts.lock().unwrap().len();
    let skipped = write(&mut transport, &record("INFO", "during the pause")).unwrap_err();
    assert!(skipped.contains("sends paused until"), "{skipped}");
    assert_eq!(
        poster.posts.lock().unwrap().len(),
        sent_before,
        "no request is made while paused"
    );

    *now.lock().unwrap() = 1000.0 + 121.0;
    write(&mut transport, &record("INFO", "after the pause")).unwrap();
    assert_eq!(poster.posts.lock().unwrap().len(), sent_before + 1);
    assert!(new_relic_transport(
        "LIC",
        "MOON",
        options(1),
        Box::new(poster),
        Arc::new(|| 0.0)
    )
    .is_err());
}

#[test]
fn retry_after_reads_seconds_dates_and_falls_back_to_a_minute() {
    assert_eq!(retry_after_seconds(Some("30"), 0.0), 30.0);
    assert_eq!(retry_after_seconds(None, 0.0), 60.0);
    assert_eq!(retry_after_seconds(Some("soon"), 0.0), 60.0);
    // 1994-11-06 08:49:37 GMT is 784111777; ten seconds before it:
    assert_eq!(
        retry_after_seconds(Some("Sun, 06 Nov 1994 08:49:37 GMT"), 784_111_767.0),
        10.0
    );
    assert_eq!(
        retry_after_seconds(Some("Sun, 06 Nov 1994 08:49:37 GMT"), 784_111_800.0),
        0.0
    );
}

#[test]
fn application_insights_posts_trace_envelopes() {
    let poster = Recording::default();
    let mut transport = app_insights_transport("IKEY", options(1), Box::new(poster.clone()));
    write(&mut transport, &record("WARN", "careful")).unwrap();
    let posts = poster.posts.lock().unwrap();
    assert_eq!(posts[0].0, "https://dc.services.visualstudio.com/v2/track");
    let envelope: Value = serde_json::from_slice(&posts[0].2).unwrap();
    assert_eq!(envelope["name"], "Microsoft.ApplicationInsights.Trace");
    assert_eq!(envelope["iKey"], "IKEY");
    assert_eq!(envelope["data"]["baseData"]["severityLevel"], 2);
    assert_eq!(envelope["data"]["baseData"]["message"], "careful");
    assert_eq!(envelope["data"]["baseData"]["properties"]["user"], "u1");
}

#[test]
fn a_huge_or_unusable_retry_after_is_capped_or_ignored() {
    assert_eq!(retry_after_seconds(Some("1e12"), 0.0), 3600.0);
    assert_eq!(retry_after_seconds(Some("inf"), 0.0), 60.0);
    assert_eq!(retry_after_seconds(Some("NaN"), 0.0), 60.0);
    assert_eq!(retry_after_seconds(Some("-5"), 0.0), 0.0);
    assert_eq!(
        retry_after_seconds(Some("Sun, 06 Nov 2095 08:49:37 GMT"), 0.0),
        3600.0
    );
}

#[test]
fn datadog_always_sends_json_whatever_the_formatter() {
    let poster = Recording::default();
    let options = HttpOptions {
        formatter: logquill_runtime::Formatter::Logfmt,
        ..options(2)
    };
    let mut transport =
        datadog_transport("KEY", "datadoghq.com", options, Box::new(poster.clone()));
    write(&mut transport, &record("INFO", "a")).unwrap();
    write(&mut transport, &record("INFO", "b")).unwrap();
    let posts = poster.posts.lock().unwrap().clone();
    let body: Value = serde_json::from_slice(&posts[0].2).expect("the body is valid JSON");
    assert_eq!(body[0]["message"], "a");
    assert_eq!(body[1]["message"], "b");
}
