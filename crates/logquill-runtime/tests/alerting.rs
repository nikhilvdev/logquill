//! Alert senders, the timer-driven alerting service, and the metrics backends.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, UdpSocket};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use logquill_core::AlertOptions;
use logquill_runtime::{
    pagerduty_event, slack_text, AlertSender, Alerting, EmailOptions, EmailSender, HttpPost,
    HttpResponse, MetricsBackend, MetricsPlugin, OtlpMetricsBackend, PagerDutySender,
    PushgatewayBackend, SlackSender, StatsdBackend, UreqPost,
};
use serde_json::{json, Value};

fn record(level: &str, message: &str) -> Value {
    json!({"schema_version": "2.0", "timestamp": "2026-01-01T00:00:00.000Z", "level": level,
           "logger": "svc", "message": message, "meta": {"user": "u1", "duration_ms": 12.5}})
}

type Post = (String, Vec<(String, String)>, Vec<u8>);

#[derive(Clone, Default)]
struct Recording {
    posts: Arc<Mutex<Vec<Post>>>,
    status: Arc<Mutex<u16>>,
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
            .push((url.into(), headers.to_vec(), body.to_vec()));
        let status = *self.status.lock().unwrap();
        Ok(HttpResponse {
            status: if status == 0 { 200 } else { status },
            retry_after: None,
        })
    }

    fn put(
        &mut self,
        url: &str,
        headers: &[(String, String)],
        body: &[u8],
        t: Duration,
    ) -> Result<HttpResponse, String> {
        self.post(url, headers, body, t)
    }
}

#[test]
fn slack_and_pagerduty_format_the_alert_and_explain_a_rejection() {
    assert_eq!(slack_text(&record("ERROR", "boom"), 1), "[ERROR] svc: boom");
    assert_eq!(
        slack_text(&record("ERROR", "boom"), 7),
        "[ERROR] svc: boom (x7)"
    );

    let poster = Recording::default();
    let mut slack = SlackSender {
        webhook_url: "https://hooks.slack.test/x".into(),
        timeout: Duration::from_secs(5),
        poster: Box::new(poster.clone()),
    };
    slack.send(&record("ERROR", "boom"), 3).unwrap();
    let body: Value = serde_json::from_slice(&poster.posts.lock().unwrap()[0].2).unwrap();
    assert_eq!(body, json!({"text": "[ERROR] svc: boom (x3)"}));
    *poster.status.lock().unwrap() = 404;
    assert!(slack
        .send(&record("ERROR", "boom"), 1)
        .unwrap_err()
        .contains("webhook returned HTTP 404"));

    let event = pagerduty_event("RK", &record("FATAL", "down"), 2);
    assert_eq!(event["event_action"], "trigger");
    assert_eq!(event["payload"]["severity"], "critical");
    assert_eq!(event["payload"]["summary"], "svc: down (x2)");
    assert_eq!(event["payload"]["custom_details"]["occurrences"], 2);
    assert_eq!(event["payload"]["custom_details"]["user"], "u1");
    assert_eq!(
        pagerduty_event("RK", &record("ERROR", "e"), 1)["payload"]["severity"],
        "error"
    );

    let pd_poster = Recording::default();
    *pd_poster.status.lock().unwrap() = 400;
    let mut pagerduty =
        PagerDutySender::new("RK", Duration::from_secs(5), Box::new(pd_poster.clone()));
    assert!(pagerduty
        .send(&record("ERROR", "e"), 1)
        .unwrap_err()
        .contains("Events API returned HTTP 400"));
    assert_eq!(
        pd_poster.posts.lock().unwrap()[0].0,
        "https://events.pagerduty.com/v2/enqueue"
    );
}

/// Accepts one SMTP conversation and returns the message data.
fn smtp_server() -> (u16, thread::JoinHandle<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let handle = thread::spawn(move || {
        let (stream, _) = listener.accept().unwrap();
        let mut writer = stream.try_clone().unwrap();
        let mut reader = BufReader::new(stream);
        writer.write_all(b"220 test ESMTP\r\n").unwrap();
        let mut data = String::new();
        let mut in_data = false;
        let mut line = String::new();
        loop {
            line.clear();
            if reader.read_line(&mut line).unwrap() == 0 {
                break;
            }
            if in_data {
                if line == ".\r\n" {
                    in_data = false;
                    writer.write_all(b"250 queued\r\n").unwrap();
                } else {
                    data.push_str(&line);
                }
                continue;
            }
            let command = line.trim_end().to_ascii_uppercase();
            let reply: &[u8] = if command.starts_with("EHLO") || command.starts_with("HELO") {
                b"250 test\r\n"
            } else if command.starts_with("DATA") {
                in_data = true;
                b"354 go ahead\r\n"
            } else if command.starts_with("QUIT") {
                let _ = writer.write_all(b"221 bye\r\n");
                break;
            } else {
                b"250 ok\r\n"
            };
            writer.write_all(reply).unwrap();
        }
        data
    });
    (port, handle)
}

#[test]
fn email_is_sent_over_smtp_with_the_documented_subject_and_body() {
    let (port, server) = smtp_server();
    let mut sender = EmailSender::new(EmailOptions {
        smtp_host: "127.0.0.1".into(),
        smtp_port: port,
        from_addr: "alerts@example.com".into(),
        to_addrs: vec!["ops@example.com".into()],
        username: None,
        password: None,
        use_tls: false,
        timeout: Duration::from_secs(5),
    });
    sender.send(&record("ERROR", "boom"), 4).unwrap();
    let data = server.join().unwrap();
    assert!(data.contains("Subject: [ERROR] svc (x4)"), "{data}");
    assert!(
        data.contains("occurrences: 4") && data.contains("timestamp: 2026-01-01T00:00:00.000Z"),
        "{data}"
    );
    assert!(data.contains("boom"), "{data}");
    let (subject, _) = EmailSender::compose(&record("WARN", "w"), 1);
    assert_eq!(subject, "[WARN] svc");
}

#[derive(Clone, Default)]
struct Collect {
    sent: Arc<Mutex<Vec<(String, u64)>>>,
    delay: Duration,
}

impl AlertSender for Collect {
    fn send(&mut self, record: &Value, occurrences: u64) -> Result<(), String> {
        thread::sleep(self.delay);
        self.sent
            .lock()
            .unwrap()
            .push((record["message"].as_str().unwrap().to_string(), occurrences));
        Ok(())
    }
}

fn quiet() -> Arc<dyn Fn(&str) + Send + Sync> {
    Arc::new(|_| {})
}

#[test]
fn a_repeated_error_alerts_once_at_once_and_once_more_with_the_count() {
    let collect = Collect::default();
    let options = AlertOptions {
        window_seconds: 0.3,
        ..AlertOptions::default()
    };
    let alerting = Alerting::new(options, Box::new(collect.clone()), quiet()).unwrap();
    for _ in 0..3 {
        alerting.observe(&record("ERROR", "db down"));
    }
    alerting.observe(&record("INFO", "ignored"));
    alerting.flush(Some(Duration::from_secs(5)));
    assert_eq!(
        *collect.sent.lock().unwrap(),
        vec![("db down".to_string(), 1)]
    );
    thread::sleep(Duration::from_millis(700));
    alerting.flush(Some(Duration::from_secs(5)));
    assert_eq!(
        *collect.sent.lock().unwrap(),
        vec![("db down".to_string(), 1), ("db down".to_string(), 3)]
    );
    alerting.close();
}

#[test]
fn a_slow_destination_never_blocks_the_caller_and_closing_cancels_summaries() {
    let collect = Collect {
        delay: Duration::from_millis(40),
        ..Collect::default()
    };
    let options = AlertOptions {
        window_seconds: 30.0,
        ..AlertOptions::default()
    };
    let alerting = Alerting::new(options, Box::new(collect.clone()), quiet()).unwrap();
    let started = Instant::now();
    for n in 0..200 {
        alerting.observe(&record("ERROR", &format!("distinct error {n}")));
        alerting.observe(&record("ERROR", &format!("distinct error {n}")));
    }
    assert!(
        started.elapsed() < Duration::from_millis(300),
        "observe waited on the sender: {:?}",
        started.elapsed()
    );
    alerting.close();
    assert!(
        collect.sent.lock().unwrap().iter().all(|(_, n)| *n == 1),
        "the pending summaries were cancelled, not sent"
    );
}

#[test]
fn statsd_sends_counters_and_histograms_with_tags_and_a_prefix() {
    let socket = UdpSocket::bind("127.0.0.1:0").unwrap();
    socket
        .set_read_timeout(Some(Duration::from_secs(5)))
        .unwrap();
    let backend = StatsdBackend::new(
        "127.0.0.1",
        socket.local_addr().unwrap().port(),
        Some("app"),
    );
    let plugin = MetricsPlugin::new(Box::new(backend));
    plugin.observe(&record("ERROR", "boom"));
    let mut buffer = [0u8; 512];
    let n = socket.recv(&mut buffer).unwrap();
    assert_eq!(
        String::from_utf8_lossy(&buffer[..n]),
        "app.logquill.errors:1|c|#logger:svc"
    );
    let n = socket.recv(&mut buffer).unwrap();
    assert_eq!(
        String::from_utf8_lossy(&buffer[..n]),
        "app.logquill.duration_ms:12.5|h|#logger:svc"
    );
    plugin.observe(&record("INFO", "fine"));
    socket
        .set_read_timeout(Some(Duration::from_millis(200)))
        .unwrap();
    let n = socket.recv(&mut buffer).unwrap(); // only the duration: INFO is not an error
    assert!(String::from_utf8_lossy(&buffer[..n]).contains("duration_ms"));
    assert!(socket.recv(&mut buffer).is_err());
}

type Seen = Arc<Mutex<Vec<(String, String)>>>;

fn serve_put(status: u16) -> (String, Seen) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let seen = Arc::new(Mutex::new(Vec::new()));
    let log = seen.clone();
    thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { break };
            let mut data = Vec::new();
            let mut chunk = [0u8; 4096];
            let length = loop {
                let n = stream.read(&mut chunk).unwrap_or(0);
                if n == 0 {
                    break 0;
                }
                data.extend_from_slice(&chunk[..n]);
                if let Some(at) = data.windows(4).position(|w| w == b"\r\n\r\n") {
                    let head = String::from_utf8_lossy(&data[..at]).to_ascii_lowercase();
                    let len = head
                        .lines()
                        .find_map(|l| {
                            l.strip_prefix("content-length:")
                                .map(|v| v.trim().parse().unwrap_or(0))
                        })
                        .unwrap_or(0);
                    break at + 4 + len;
                }
            };
            while data.len() < length {
                let n = stream.read(&mut chunk).unwrap_or(0);
                if n == 0 {
                    break;
                }
                data.extend_from_slice(&chunk[..n]);
            }
            let text = String::from_utf8_lossy(&data).to_string();
            let request_line = text.lines().next().unwrap_or("").to_string();
            let body = text.split("\r\n\r\n").nth(1).unwrap_or("").to_string();
            log.lock().unwrap().push((request_line, body));
            let _ = stream.write_all(
                format!("HTTP/1.1 {status} X\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    .as_bytes(),
            );
        }
    });
    (url, seen)
}

#[test]
fn the_pushgateway_accumulates_sanitises_and_puts_text_metrics() {
    let (url, seen) = serve_put(200);
    let mut backend =
        PushgatewayBackend::new(&url, "myjob", Duration::from_secs(5), Box::new(UreqPost));
    backend.flush().unwrap();
    assert!(
        seen.lock().unwrap().is_empty(),
        "nothing recorded, nothing sent"
    );
    let tags = [
        ("logger".to_string(), "svc".to_string()),
        ("1st-tag".to_string(), "a\"b".to_string()),
    ];
    backend.increment("logquill.errors", &tags);
    backend.increment("logquill.errors", &tags);
    backend.observe("logquill.duration_ms", 10.0, &tags);
    backend.observe("logquill.duration_ms", 2.5, &tags);
    backend.close().unwrap();
    let seen = seen.lock().unwrap();
    assert!(
        seen[0].0.starts_with("PUT /metrics/job/myjob "),
        "{}",
        seen[0].0
    );
    let body = &seen[0].1;
    assert!(
        body.contains("logquill_errors{_1st_tag=\"a\\\"b\",logger=\"svc\"} 2.0"),
        "{body}"
    );
    assert!(
        body.contains("logquill_duration_ms_sum{_1st_tag=\"a\\\"b\",logger=\"svc\"} 12.5"),
        "{body}"
    );
    assert!(
        body.contains("logquill_duration_ms_count{_1st_tag=\"a\\\"b\",logger=\"svc\"} 2"),
        "{body}"
    );
}

#[test]
fn a_rejecting_pushgateway_is_reported() {
    let (url, _) = serve_put(500);
    let mut backend =
        PushgatewayBackend::new(&url, "j", Duration::from_secs(5), Box::new(UreqPost));
    backend.increment("c", &[]);
    assert!(backend
        .flush()
        .unwrap_err()
        .contains("pushgateway returned HTTP 500"));
}

#[test]
fn otlp_metrics_are_posted_as_cumulative_sums_and_histograms() {
    let poster = Recording::default();
    let mut backend = OtlpMetricsBackend::new(
        "http://localhost:4318/v1/metrics",
        "svc",
        Duration::from_secs(5),
        Box::new(poster.clone()),
    );
    assert!(backend.document(0).is_none());
    backend.increment("logquill.errors", &[("logger".into(), "svc".into())]);
    backend.observe(
        "logquill.duration_ms",
        4.0,
        &[("logger".into(), "svc".into())],
    );
    backend.flush().unwrap();
    let body: Value = serde_json::from_slice(&poster.posts.lock().unwrap()[0].2).unwrap();
    let metrics = &body["resourceMetrics"][0]["scopeMetrics"][0]["metrics"];
    assert_eq!(metrics[0]["name"], "logquill.errors");
    assert_eq!(metrics[0]["sum"]["isMonotonic"], true);
    assert_eq!(metrics[1]["histogram"]["dataPoints"][0]["count"], "1");
    assert_eq!(metrics[1]["histogram"]["dataPoints"][0]["sum"], 4.0);
}

#[test]
fn closing_an_idle_alerting_service_never_waits_for_the_timer() {
    // The timer sleeps up to an hour when nothing is pending. A missed wake-up on close would make
    // this loop hang, so a few hundred opens and closes shake it out.
    let started = Instant::now();
    for _ in 0..300 {
        let alerting = Alerting::new(
            AlertOptions::default(),
            Box::new(Collect::default()),
            quiet(),
        )
        .unwrap();
        alerting.close();
    }
    assert!(
        started.elapsed() < Duration::from_secs(20),
        "close waited for the timer"
    );
}

#[test]
fn a_window_opened_while_the_timer_sleeps_still_sends_its_summary_on_time() {
    let collect = Collect::default();
    let options = AlertOptions {
        window_seconds: 0.4,
        ..AlertOptions::default()
    };
    let alerting = Alerting::new(options, Box::new(collect.clone()), quiet()).unwrap();
    thread::sleep(Duration::from_millis(150)); // the timer is now waiting with nothing pending
    for _ in 0..3 {
        alerting.observe(&record("ERROR", "late burst"));
    }
    thread::sleep(Duration::from_millis(1200));
    alerting.flush(Some(Duration::from_secs(5)));
    assert_eq!(
        *collect.sent.lock().unwrap(),
        vec![("late burst".to_string(), 1), ("late burst".to_string(), 3)]
    );
}

#[test]
fn email_refuses_to_send_credentials_without_tls() {
    let mut sender = EmailSender::new(EmailOptions {
        smtp_host: "127.0.0.1".into(),
        smtp_port: 1, // never reached: the refusal comes first
        from_addr: "alerts@example.com".into(),
        to_addrs: vec!["ops@example.com".into()],
        username: Some("user".into()),
        password: Some("secret".into()),
        use_tls: false,
        timeout: Duration::from_secs(1),
    });
    let error = sender.send(&record("ERROR", "boom"), 1).unwrap_err();
    assert!(
        error.contains("without TLS") && error.contains("use_tls"),
        "{error}"
    );
}

#[test]
fn metrics_backends_stop_adding_series_at_their_cap_but_keep_counting_known_ones() {
    let mut backend = PushgatewayBackend::new(
        "http://127.0.0.1:1",
        "j",
        Duration::from_secs(1),
        Box::new(UreqPost),
    );
    backend.max_series = 2;
    for logger in ["a", "b", "c", "a"] {
        backend.increment("m", &[("logger".into(), logger.into())]);
    }
    assert_eq!(backend.dropped_samples(), 1);
    let body = backend.body().unwrap();
    assert!(body.contains("logger=\"a\"} 2"), "{body}");
    assert!(
        body.contains("logger=\"b\"") && !body.contains("logger=\"c\""),
        "{body}"
    );

    let mut otlp = OtlpMetricsBackend::new(
        "http://localhost:4318/v1/metrics",
        "svc",
        Duration::from_secs(1),
        Box::new(Recording::default()),
    );
    otlp.max_series = 1;
    otlp.increment("first", &[]);
    otlp.observe("second", 1.0, &[]);
    assert_eq!(otlp.dropped_samples(), 1);
}
