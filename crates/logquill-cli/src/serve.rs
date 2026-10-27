//! `logquill serve`: the local trace viewer. A tiny HTTP/1.1 server on `std::net`, one embedded
//! page, and four routes. Nothing leaves the machine; there is no authentication, so a
//! non-loopback `--host` exposes the logs to the network.

use std::io::{BufRead, BufReader, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::Arc;
use std::thread;
use std::time::Duration;

use serde_json::{json, Value};

use crate::runs::{search, summarise, Query};
use crate::source::Source;
use crate::trace;

const PAGE: &str = include_str!("../assets/trace_viewer.html");
const MAX_LIMIT: usize = 500;

pub struct Response {
    pub status: u16,
    pub content_type: &'static str,
    pub body: Vec<u8>,
}

fn json_response(status: u16, value: &Value) -> Response {
    Response {
        status,
        content_type: "application/json",
        body: value.to_string().into_bytes(),
    }
}

fn error(status: u16, message: &str) -> Response {
    json_response(status, &json!({ "error": message }))
}

fn percent_decode(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'+' => out.push(b' '),
            b'%' if i + 2 < bytes.len()
                && bytes[i + 1].is_ascii_hexdigit()
                && bytes[i + 2].is_ascii_hexdigit() =>
            {
                let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).unwrap_or("00");
                out.push(u8::from_str_radix(hex, 16).unwrap_or(b'?'));
                i += 2;
            }
            other => out.push(other),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

fn query_pairs(query: &str) -> Vec<(String, String)> {
    query
        .split('&')
        .filter(|pair| !pair.is_empty())
        .map(|pair| {
            let (key, value) = pair.split_once('=').unwrap_or((pair, ""));
            (percent_decode(key), percent_decode(value))
        })
        .collect()
}

/// The answer to `GET target`, where `runs` is the snapshot taken when the server started.
pub fn route(source: &Source, runs: &[Value], target: &str) -> Response {
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    let params = query_pairs(query);
    let param = |name: &str| {
        params
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value.as_str())
            .filter(|value| !value.is_empty())
    };
    match path {
        "/" => Response {
            status: 200,
            content_type: "text/html; charset=utf-8",
            body: PAGE.as_bytes().to_vec(),
        },
        "/api/runs" => json_response(200, &Value::Array(runs.to_vec())),
        "/api/trace" => {
            let Some(run_id) = param("run_id") else {
                return error(400, "run_id is required");
            };
            match trace::load(source, run_id, None) {
                Ok(trace) => json_response(
                    200,
                    &Value::Array(trace.roots.iter().map(|n| n.to_node_value()).collect()),
                ),
                Err(e) => error(500, &e),
            }
        }
        "/api/search" => {
            let limit = match param("limit").map(str::parse::<i64>) {
                None => MAX_LIMIT,
                Some(Ok(n)) => n.clamp(1, MAX_LIMIT as i64) as usize,
                Some(Err(_)) => return error(400, "limit must be a whole number"),
            };
            let query = Query {
                text: param("q").unwrap_or("").to_lowercase(),
                level: param("level").map(str::to_string),
                run_id: param("run_id").map(str::to_string),
                limit,
            };
            match search(source, &query) {
                Ok(hits) => json_response(200, &Value::Array(hits)),
                Err(e) => error(500, &e),
            }
        }
        _ => error(404, "not found"),
    }
}

fn reason(status: u16) -> &'static str {
    match status {
        200 => "OK",
        400 => "Bad Request",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        _ => "Internal Server Error",
    }
}

/// Whether a `Host` header names this machine. Checked on loopback servers so a web page cannot
/// reach the logs through a DNS name that resolves to 127.0.0.1 (DNS rebinding).
fn local_host(header: &str) -> bool {
    let name = match header.strip_prefix('[') {
        Some(rest) => rest.split_once(']').map_or(rest, |(inside, _)| inside),
        None => header.split(':').next().unwrap_or(""),
    };
    matches!(name, "localhost" | "127.0.0.1" | "::1")
}

fn handle(mut stream: TcpStream, source: &Source, runs: &[Value], loopback: bool) {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let mut reader = BufReader::new(&stream);
    let mut request_line = String::new();
    if reader.read_line(&mut request_line).unwrap_or(0) == 0 {
        return;
    }
    let mut host = None;
    for _ in 0..64 {
        let mut header = String::new();
        if reader.read_line(&mut header).unwrap_or(0) == 0 || header.trim().is_empty() {
            break;
        }
        if let Some((name, value)) = header.split_once(':') {
            if name.eq_ignore_ascii_case("host") {
                host = Some(value.trim().to_string());
            }
        }
    }
    let mut parts = request_line.split_whitespace();
    let (method, target) = (parts.next().unwrap_or(""), parts.next().unwrap_or("/"));
    let response = if method != "GET" {
        error(405, "only GET is supported")
    } else if loopback && host.as_deref().is_some_and(|h| !local_host(h)) {
        error(
            403,
            "this server only answers requests addressed to localhost",
        )
    } else {
        route(source, runs, target)
    };
    let head = format!(
        "HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n",
        response.status,
        reason(response.status),
        response.content_type,
        response.body.len()
    );
    let _ = stream.write_all(head.as_bytes());
    let _ = stream.write_all(&response.body);
}

/// Runs the server until interrupted; returns the exit code.
pub fn run(source: Source, host: &str, port: u16, out: &mut dyn Write) -> u8 {
    if !source.path().exists() {
        eprintln!("logquill serve: no such file: {}", source.path().display());
        return 1;
    }
    let runs = match summarise(&source) {
        Ok(runs) => runs,
        Err(e) => {
            eprintln!("logquill serve: {e}");
            return 1;
        }
    };
    let listener = match TcpListener::bind((host, port)) {
        Ok(listener) => listener,
        Err(e) => {
            eprintln!("logquill serve: couldn't listen on {host}:{port} \u{2014} {e}");
            return 1;
        }
    };
    let Ok(address) = listener.local_addr() else {
        eprintln!("logquill serve: couldn't read the address it is listening on");
        return 1;
    };
    let shown = if address.is_ipv6() {
        format!("[{}]", address.ip())
    } else {
        address.ip().to_string()
    };
    let _ = writeln!(
        out,
        "logquill serve: {} run(s) found in {}",
        runs.len(),
        source.path().display()
    );
    let _ = writeln!(
        out,
        "logquill serve: listening on http://{shown}:{}/ \u{2014} Ctrl+C to stop",
        address.port()
    );
    let _ = out.flush();
    let shared = Arc::new((source, runs));
    let loopback = address.ip().is_loopback();
    for stream in listener.incoming().flatten() {
        let shared = shared.clone();
        thread::spawn(move || handle(stream, &shared.0, &shared.1, loopback));
    }
    0
}
