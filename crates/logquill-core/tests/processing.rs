//! Runs the processing groups of the shared corpus (`schema/corpus/`): trace headers and ids,
//! redaction, line formats, ECS, GenAI attributes, SQL and graph rows, trace trees, and the
//! stateful plugins stepped through an injected clock and random draw.

use std::{fs, path::PathBuf};

use logquill_core::*;
use serde_json::{json, Map, Value};

struct Case {
    name: String,
    input: Value,
    expected: Value,
}

fn load(group: &str) -> Vec<Case> {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../schema/corpus")
        .join(format!("{group}.json"));
    let text = fs::read_to_string(path).expect("a corpus file");
    let file: Value = serde_json::from_str(&text).expect("valid JSON");
    file["cases"]
        .as_array()
        .expect("cases")
        .iter()
        .map(|case| Case {
            name: case["name"].as_str().expect("a name").to_string(),
            input: case["input"].clone(),
            expected: case["expected"].clone(),
        })
        .collect()
}

fn check(group: &str, run: impl Fn(&Value) -> Value) {
    for case in load(group) {
        assert_eq!(run(&case.input), case.expected, "{group}: {}", case.name);
    }
}

/// Numbers compared by value, so a clock reading of `0` and `0.0` agree (scenario groups only).
fn loose(value: &Value) -> Value {
    match value {
        Value::Number(n) => n
            .to_string()
            .parse::<f64>()
            .ok()
            .and_then(serde_json::Number::from_f64)
            .map_or(Value::Null, Value::Number),
        Value::Array(items) => Value::Array(items.iter().map(loose).collect()),
        Value::Object(map) => {
            Value::Object(map.iter().map(|(k, v)| (k.clone(), loose(v))).collect())
        }
        other => other.clone(),
    }
}

fn check_loose(group: &str, run: impl Fn(&Value) -> Value) {
    for case in load(group) {
        assert_eq!(
            loose(&run(&case.input)),
            loose(&case.expected),
            "{group}: {}",
            case.name
        );
    }
}

fn str_of<'a>(value: &'a Value, key: &str) -> Option<&'a str> {
    value.get(key).and_then(Value::as_str)
}

fn object(value: &Value, key: &str) -> Map<String, Value> {
    value
        .get(key)
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default()
}

#[test]
fn trace_headers() {
    check(
        "trace-headers",
        |input| json!({"trace_id": parse_trace_header(str_of(input, "header").unwrap())}),
    );
}

#[test]
fn otel_ids() {
    check("otel-ids", |input| {
        let id = match str_of(input, "op").unwrap() {
            "span_id" => Some(span_id_for(str_of(input, "value").unwrap())),
            "trace_id" => trace_id_for(&object(input, "meta")),
            _ => Some(trace_id_from_parent(str_of(input, "value").unwrap())),
        };
        json!({"id": id})
    });
}

fn classes_of(input: &Value) -> Vec<FieldClass> {
    input["classes"]
        .as_array()
        .map(|names| {
            names
                .iter()
                .map(|n| match n.as_str().unwrap() {
                    "secret" => FieldClass::Secret,
                    "pii" => FieldClass::Pii,
                    _ => FieldClass::Content,
                })
                .collect()
        })
        .unwrap_or_default()
}

#[test]
fn redaction() {
    check("redaction", |input| {
        let replacement = str_of(input, "replacement").unwrap_or("***");
        let op = str_of(input, "op").unwrap();
        if op.starts_with("redact") {
            let keys: Vec<String> = match input.get("keys").and_then(Value::as_array) {
                Some(list) => list
                    .iter()
                    .map(|k| k.as_str().unwrap().to_string())
                    .collect(),
                None => DEFAULT_KEYS.iter().map(|k| k.to_string()).collect(),
            };
            let redactor = KeyRedactor::new(
                keys.iter().map(String::as_str),
                &classes_of(input),
                replacement,
            );
            if op == "redact_local" {
                return json!({"text": redactor.redact_local(
                    str_of(input, "name").unwrap(),
                    str_of(input, "text").unwrap(),
                )});
            }
            let mut meta = object(input, "meta");
            redactor.redact_meta(&mut meta);
            return json!({"meta": meta});
        }
        let redactor = PiiRedactor::new(replacement);
        if op == "pii_local" {
            return json!({"text": redactor.redact_text(str_of(input, "text").unwrap())});
        }
        let mut meta = object(input, "meta");
        redactor.redact_meta(&mut meta);
        json!({"meta": meta})
    });
}

#[test]
fn formatters() {
    check("formatters", |input| {
        let op = str_of(input, "op").unwrap();
        if op == "parse_logfmt" {
            return json!({"fields": parse_logfmt(str_of(input, "line").unwrap())});
        }
        let record: Value = match str_of(input, "record_json") {
            // serde cannot hold NaN or Infinity; an overflowing literal reads as the same value.
            Some(text) => serde_json::from_str(
                &text
                    .replace("-Infinity", "-1e999")
                    .replace("Infinity", "1e999")
                    .replace("NaN", "1e999"),
            )
            .unwrap(),
            None => input["record"].clone(),
        };
        let line = match op {
            "json" => format_json(&record),
            "text" => format_text(&record),
            _ => format_logfmt(&record),
        };
        json!({"line": line})
    });
}

#[test]
fn ecs() {
    check(
        "ecs",
        |input| json!({"document": to_ecs_document(&input["record"])}),
    );
}

#[test]
fn semconv() {
    check("semconv", |input| {
        let op = str_of(input, "op").unwrap();
        match op {
            "resolve" => match resolve_convention(str_of(input, "explicit")) {
                Ok(convention) => {
                    let names: Map<String, Value> = convention
                        .names()
                        .into_iter()
                        .map(|(k, v)| (k.to_string(), json!(v)))
                        .collect();
                    json!({"version": convention.version(), "names": names})
                }
                Err(error) => json!({"error": error}),
            },
            "capture" => {
                let env = object(input, "env");
                json!({"enabled": capture_content_enabled(env.get(CAPTURE_ENV).and_then(Value::as_str))})
            }
            "epoch" => {
                let ns = epoch_ns(str_of(input, "timestamp").unwrap());
                json!({"ns": ns.map(|n| serde_json::from_str::<Value>(&n.to_string()).unwrap())})
            }
            "classify" => json!({
                "span": is_span_record(&input["record"]),
                "llm": is_llm_record(&input["record"]),
                "tool": is_tool_record(&input["record"]),
            }),
            _ => {
                let convention =
                    resolve_convention(Some(str_of(input, "convention").unwrap_or("latest")))
                        .unwrap();
                if op == "log_attributes" {
                    return json!({"attributes": log_attributes(&input["record"], convention)});
                }
                let options = SpecOptions {
                    default_provider: str_of(input, "default_provider")
                        .unwrap_or("unknown")
                        .to_string(),
                    capture_content: input
                        .get("capture_content")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                };
                json!({"span": export_spec(&input["record"], convention, &options).map(|s| s.to_value())})
            }
        }
    });
}

#[test]
fn sql_rows() {
    check("sql-rows", |input| {
        if str_of(input, "op") == Some("ddl") {
            let dialect = match str_of(input, "dialect").unwrap_or("sqlite") {
                "postgres" => Dialect::Postgres,
                "mysql" => Dialect::Mysql,
                _ => Dialect::Sqlite,
            };
            return json!({"sql": create_table_sql(dialect, str_of(input, "table_name").unwrap_or("logs"))});
        }
        json!({"row": sql_row(&input["record"])})
    });
}

#[test]
fn graph_rows() {
    check("graph-rows", |input| {
        if str_of(input, "op") == Some("statement") {
            return json!({"cypher": CYPHER});
        }
        let mut keys = GraphKeys::default();
        if let Some(k) = str_of(input, "run_key") {
            keys.run_key = k.to_string();
        }
        if let Some(k) = str_of(input, "trace_key") {
            keys.trace_key = k.to_string();
        }
        json!({"row": graph_row(&input["record"], &keys)})
    });
}

#[test]
fn trace_tree() {
    check("trace-tree", |input| {
        let records = input["records"].as_array().unwrap();
        let trace = build_trace(records.iter(), str_of(input, "run_id").unwrap());
        json!({
            "record_count": trace.record_count,
            "roots": trace.roots.iter().map(TreeNode::to_value).collect::<Vec<_>>(),
            "text": render_tree(&trace.roots),
        })
    });
}

// ---- scenario groups ----------------------------------------------------------------------

struct Clockwork {
    clock: f64,
    draw: f64,
}

impl Clockwork {
    fn new(input: &Value) -> Clockwork {
        Clockwork {
            clock: input.get("start").and_then(Value::as_f64).unwrap_or(0.0),
            draw: 0.0,
        }
    }

    fn advance(&mut self, step: &Value) {
        self.clock = step
            .get("clock")
            .and_then(Value::as_f64)
            .unwrap_or(self.clock);
        self.draw = step.get("rng").and_then(Value::as_f64).unwrap_or(self.draw);
    }
}

fn messages(records: &[Value]) -> Vec<Value> {
    records.iter().map(|r| r["message"].clone()).collect()
}

fn level_option(options: &Value, key: &str, default: Level) -> Level {
    str_of(options, key).map_or(default, |n| Level::parse(n).unwrap())
}

fn usize_option(options: &Value, key: &str, default: usize) -> usize {
    options
        .get(key)
        .and_then(Value::as_u64)
        .map_or(default, |n| n as usize)
}

fn f64_option(options: &Value, key: &str, default: f64) -> f64 {
    options.get(key).and_then(Value::as_f64).unwrap_or(default)
}

#[test]
fn sampling() {
    check_loose("sampling", |input| {
        let o = input.get("options").cloned().unwrap_or(Value::Null);
        let mut options = SamplingOptions::new(f64_option(&o, "rate", 0.0));
        options.tail = o.get("tail").and_then(Value::as_bool).unwrap_or(false);
        if let Some(k) = str_of(&o, "trace_key") {
            options.trace_key = k.to_string();
        }
        options.elevate_at = level_option(&o, "elevate_at", Level::Error);
        options.max_buffered_records = usize_option(&o, "max_buffered_records", 1000);
        options.max_traces = usize_option(&o, "max_traces", 200);
        options.max_elevated = usize_option(&o, "max_elevated", 10_000);
        let mut sampler = match Sampling::new(options) {
            Ok(s) => s,
            Err(error) => return json!({"error": error}),
        };
        let mut time = Clockwork::new(input);
        let steps: Vec<Value> = input["steps"]
            .as_array()
            .unwrap()
            .iter()
            .map(|step| {
                time.advance(step);
                let decision = sampler.decide(&step["record"], time.draw);
                json!({"kept": decision.kept, "written": messages(&decision.released)})
            })
            .collect();
        json!({"steps": steps, "state": {
            "buffered_traces": sampler.buffered_traces(),
            "buffered_records": sampler.buffered_records(),
            "elevated": sampler.elevated(),
        }})
    });
}

#[test]
fn rate_limit() {
    check_loose("rate-limit", |input| {
        let o = input.get("options").cloned().unwrap_or(Value::Null);
        let key = match str_of(&o, "key").unwrap_or("logger_level") {
            "logger" => RateKey::Logger,
            "message" => RateKey::Message,
            _ => RateKey::LoggerLevel,
        };
        let limiter = RateLimit::new(
            o["max_records"].as_i64().unwrap().max(0) as u64,
            f64_option(&o, "per_seconds", 1.0),
            usize_option(&o, "max_keys", 1000),
            key,
        );
        let mut limiter = match limiter {
            Ok(l) => l,
            Err(error) => return json!({"error": error}),
        };
        let mut time = Clockwork::new(input);
        let steps: Vec<Value> = input["steps"]
            .as_array()
            .unwrap()
            .iter()
            .map(|step| {
                time.advance(step);
                json!({"kept": limiter.allow(&step["record"], time.clock), "written": []})
            })
            .collect();
        let windows: Vec<Value> = limiter
            .windows()
            .into_iter()
            .map(|(k, start, count)| json!([k, start, count]))
            .collect();
        json!({"steps": steps, "state": {"windows": windows}})
    });
}

#[test]
fn adaptive_sampling() {
    check_loose("adaptive-sampling", |input| {
        let o = input.get("options").cloned().unwrap_or(Value::Null);
        let d = AdaptiveOptions::default();
        let options = AdaptiveOptions {
            base_rate: f64_option(&o, "base_rate", d.base_rate),
            min_rate: f64_option(&o, "min_rate", d.min_rate),
            max_rate: f64_option(&o, "max_rate", d.max_rate),
            keep_at: level_option(&o, "keep_at", d.keep_at),
            slow_ms: f64_option(&o, "slow_ms", d.slow_ms),
            max_bytes_per_second: o
                .get("max_bytes_per_second")
                .and_then(Value::as_u64)
                .unwrap_or(d.max_bytes_per_second),
            window_seconds: f64_option(&o, "window_seconds", d.window_seconds),
        };
        let mut time = Clockwork::new(input);
        let mut sampler = match AdaptiveSampling::new(options, time.clock) {
            Ok(s) => s,
            Err(error) => return json!({"error": error}),
        };
        let steps: Vec<Value> = input["steps"]
            .as_array()
            .unwrap()
            .iter()
            .map(|step| {
                time.advance(step);
                json!({"kept": sampler.decide(&step["record"], time.clock, time.draw), "written": []})
            })
            .collect();
        let (demand, emitted) = sampler.window_bytes();
        json!({"steps": steps, "state": {
            "rate": sampler.rate(), "window_demand_bytes": demand, "window_emitted_bytes": emitted,
        }})
    });
}

#[test]
fn flight_recorder() {
    check_loose("flight-recorder", |input| {
        let o = input.get("options").cloned().unwrap_or(Value::Null);
        let d = FlightOptions::default();
        let mut recorder = FlightRecorder::new(FlightOptions {
            run_key: str_of(&o, "run_key").map_or(d.run_key.clone(), str::to_string),
            ship_at: level_option(&o, "ship_at", d.ship_at),
            flush_at: level_option(&o, "flush_at", d.flush_at),
            max_buffered_records: usize_option(&o, "max_buffered_records", d.max_buffered_records),
            max_runs: usize_option(&o, "max_runs", d.max_runs),
            max_flushed: usize_option(&o, "max_flushed", d.max_flushed),
        });
        let steps: Vec<Value> = input["steps"]
            .as_array()
            .unwrap()
            .iter()
            .map(|step| {
                let decision = recorder.decide(&step["record"]);
                json!({"kept": decision.kept, "written": messages(&decision.released)})
            })
            .collect();
        json!({"steps": steps, "state": {
            "buffered_runs": recorder.buffered_runs(),
            "buffered_records": recorder.buffered_records(),
            "flushed": recorder.flushed(),
        }})
    });
}

#[test]
fn run_summary() {
    check_loose("run-summary", |input| {
        let o = input.get("options").cloned().unwrap_or(Value::Null);
        let d = SummaryOptions::default();
        let mut summary = RunSummary::new(SummaryOptions {
            run_key: str_of(&o, "run_key").map_or(d.run_key.clone(), str::to_string),
            logger_name: str_of(&o, "logger_name").map_or(d.logger_name.clone(), str::to_string),
            max_runs: usize_option(&o, "max_runs", d.max_runs),
        });
        let steps: Vec<Value> = input["steps"]
            .as_array()
            .unwrap()
            .iter()
            .map(|step| {
                let written: Vec<Value> = summary
                    .observe(&step["record"], "t")
                    .map(|mut record| {
                        record.as_object_mut().unwrap().shift_remove("timestamp");
                        record
                    })
                    .into_iter()
                    .collect();
                json!({"written": written})
            })
            .collect();
        json!({"steps": steps, "state": {"open_runs": summary.open_runs()}})
    });
}

#[test]
fn run_plugin() {
    check_loose("run-plugin", |input| {
        let given = input.get("options").and_then(|o| str_of(o, "run_id"));
        let mut stamp = RunStamp::new(given, || "generated".to_string());
        let generated = given.is_none_or(str::is_empty);
        let steps: Vec<Value> = input["steps"]
            .as_array()
            .unwrap()
            .iter()
            .map(|step| {
                let mut meta = object(&step["record"], "meta");
                stamp.stamp(&mut meta);
                if generated && meta.get("run_id").and_then(Value::as_str) == Some("generated") {
                    meta.insert("run_id".into(), json!("<generated>"));
                }
                json!({"kept": true, "meta": meta, "written": []})
            })
            .collect();
        json!({"steps": steps, "state": {"step": stamp.steps()}})
    });
}

#[test]
fn alert_dedupe() {
    check_loose("alert-dedupe", |input| {
        let o = input.get("options").cloned().unwrap_or(Value::Null);
        let d = AlertOptions::default();
        let mut dedupe = AlertDedupe::new(AlertOptions {
            threshold: level_option(&o, "threshold", d.threshold),
            window_seconds: f64_option(&o, "dedupe_window_seconds", d.window_seconds),
            max_tracked_keys: usize_option(&o, "max_tracked_keys", d.max_tracked_keys),
            by_message: str_of(&o, "key") == Some("message"),
        });
        let alerts = |list: Vec<Alert>| -> Vec<Value> {
            list.into_iter()
                .map(|a| json!({"message": a.message, "occurrences": a.occurrences}))
                .collect()
        };
        let steps: Vec<Value> = input["steps"]
            .as_array()
            .unwrap()
            .iter()
            .map(|step| {
                let at = step["at"].as_f64().unwrap();
                let sent = match step.get("record") {
                    Some(record) => dedupe.observe(record, at),
                    None => dedupe.advance(at),
                };
                json!({"sent": alerts(sent)})
            })
            .collect();
        let after = if let Some(end) = input.get("end").and_then(Value::as_f64) {
            alerts(dedupe.advance(end))
        } else {
            if input.get("close").and_then(Value::as_bool) == Some(true) {
                dedupe.close();
            }
            Vec::new()
        };
        json!({"steps": steps, "after": after, "tracked": dedupe.tracked()})
    });
}
