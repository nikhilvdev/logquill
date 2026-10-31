//! What the viewer lists and searches: one summary per run, and the search over records.

use std::collections::BTreeMap;
use std::ops::ControlFlow;

use logquill_core::json_line;
use serde_json::{json, Value};

use crate::source::{run_of, Source};

#[derive(Default)]
struct Run {
    record_count: u64,
    start: Option<String>,
    end: Option<String>,
    level_counts: BTreeMap<String, u64>,
    tokens_in: i128,
    tokens_out: i128,
    cost_usd: f64,
    agent_name: Option<String>,
    error_count: u64,
}

/// A whole number that is not a boolean.
fn integer(value: Option<&Value>) -> i128 {
    match value {
        Some(Value::Number(n)) => n.to_string().parse().unwrap_or(0),
        _ => 0,
    }
}

impl Run {
    fn add(&mut self, record: &Value) {
        self.record_count += 1;
        if let Some(timestamp) = record.get("timestamp").and_then(Value::as_str) {
            if self.start.as_deref().map_or(true, |s| timestamp < s) {
                self.start = Some(timestamp.to_string());
            }
            if self.end.as_deref().map_or(true, |e| timestamp > e) {
                self.end = Some(timestamp.to_string());
            }
        }
        if let Some(level) = record.get("level").and_then(Value::as_str) {
            *self.level_counts.entry(level.to_string()).or_default() += 1;
            if level == "ERROR" || level == "FATAL" {
                self.error_count += 1;
            }
        }
        if let Some(llm) = record.get("llm").and_then(Value::as_object) {
            self.tokens_in += integer(llm.get("tokens_in"));
            self.tokens_out += integer(llm.get("tokens_out"));
            if let Some(Value::Number(cost)) = llm.get("cost_usd") {
                self.cost_usd += cost.to_string().parse::<f64>().unwrap_or(0.0);
            }
        }
        let meta = record.get("meta");
        if meta
            .and_then(|m| m.get("operation"))
            .and_then(Value::as_str)
            == Some("invoke_agent")
        {
            if let Some(name) = meta
                .and_then(|m| m.get("agent_name"))
                .and_then(Value::as_str)
            {
                self.agent_name.get_or_insert_with(|| name.to_string());
            }
        }
    }

    fn to_value(&self, run_id: &str) -> Value {
        json!({
            "run_id": run_id,
            "record_count": self.record_count,
            "start": self.start,
            "end": self.end,
            "level_counts": self.level_counts,
            "tokens_in": self.tokens_in as i64,
            "tokens_out": self.tokens_out as i64,
            "cost_usd": self.cost_usd,
            "agent_name": self.agent_name,
            "error_count": self.error_count,
        })
    }
}

/// One pass over the source: a summary per run, newest start first. Memory is one entry per run.
pub fn summarise(source: &Source) -> Result<Vec<Value>, String> {
    let mut runs: BTreeMap<String, Run> = BTreeMap::new();
    source.for_each(None, &mut |record| {
        if let Some(id) = run_of(&record) {
            runs.entry(id.to_string()).or_default().add(&record);
        }
        ControlFlow::Continue(())
    })?;
    let mut summaries: Vec<(Option<String>, Value)> = runs
        .iter()
        .map(|(id, run)| (run.start.clone(), run.to_value(id)))
        .collect();
    summaries.sort_by(|a, b| b.0.cmp(&a.0));
    Ok(summaries.into_iter().map(|(_, value)| value).collect())
}

/// What `/api/search` asks for.
pub struct Query {
    /// Lower-cased text to find in the message and meta.
    pub text: String,
    pub level: Option<String>,
    pub run_id: Option<String>,
    pub limit: usize,
}

impl Query {
    fn accepts(&self, record: &Value) -> bool {
        if let Some(level) = &self.level {
            if record.get("level").and_then(Value::as_str) != Some(level) {
                return false;
            }
        }
        if let Some(run_id) = &self.run_id {
            if run_of(record) != Some(run_id) {
                return false;
            }
        }
        if self.text.is_empty() {
            return true;
        }
        let message = record.get("message").and_then(Value::as_str).unwrap_or("");
        let meta = record.get("meta").map(json_line).unwrap_or_default();
        format!("{message} {meta}")
            .to_lowercase()
            .contains(&self.text)
    }
}

/// At most `query.limit` matching records, in file order, stopping the read as soon as it is full.
pub fn search(source: &Source, query: &Query) -> Result<Vec<Value>, String> {
    let mut hits = Vec::new();
    source.for_each(None, &mut |record| {
        if query.accepts(&record) {
            hits.push(record);
        }
        if hits.len() >= query.limit {
            ControlFlow::Break(())
        } else {
            ControlFlow::Continue(())
        }
    })?;
    Ok(hits)
}
