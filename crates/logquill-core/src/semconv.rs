//! OpenTelemetry GenAI attributes and the Elastic Common Schema mapping
//! (`docs/spec/03-privacy-tracing-otel.md`; decision D43). The only place `gen_ai.*` names live.

use serde_json::{Map, Value};

use crate::jsonx::{float_value, is_integer, json_line};

/// The semantic-conventions release the names are pinned to.
pub const CONVENTION_VERSION: &str = "1.44.0";
/// The environment variable that opts in to the latest names.
pub const OPT_IN_ENV: &str = "OTEL_SEMCONV_STABILITY_OPT_IN";
/// The environment variable that turns on prompt and completion capture.
pub const CAPTURE_ENV: &str = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT";
const ASCII_SPACE: &[char] = &[' ', '\t', '\n', '\r', '\x0b', '\x0c'];

/// A generation of attribute names. Old and new names are never emitted together.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Convention {
    Latest,
    Legacy,
}

/// Which attribute a concept is written under.
#[derive(Clone, Copy, Debug)]
pub enum Name {
    Operation,
    Provider,
    RequestModel,
    ResponseModel,
    InputTokens,
    OutputTokens,
    FinishReasons,
    AgentName,
    AgentId,
    ConversationId,
    ToolName,
    ToolCallId,
    InputMessages,
    OutputMessages,
}

impl Convention {
    /// The version string reported for this generation.
    pub fn version(self) -> &'static str {
        match self {
            Convention::Latest => CONVENTION_VERSION,
            Convention::Legacy => "legacy",
        }
    }

    /// The attribute for `name` in this generation.
    pub fn attribute(self, name: Name) -> &'static str {
        match (name, self) {
            (Name::Operation, _) => "gen_ai.operation.name",
            (Name::Provider, Convention::Latest) => "gen_ai.provider.name",
            (Name::Provider, Convention::Legacy) => "gen_ai.system",
            (Name::RequestModel, _) => "gen_ai.request.model",
            (Name::ResponseModel, _) => "gen_ai.response.model",
            (Name::InputTokens, Convention::Latest) => "gen_ai.usage.input_tokens",
            (Name::InputTokens, Convention::Legacy) => "gen_ai.usage.prompt_tokens",
            (Name::OutputTokens, Convention::Latest) => "gen_ai.usage.output_tokens",
            (Name::OutputTokens, Convention::Legacy) => "gen_ai.usage.completion_tokens",
            (Name::FinishReasons, _) => "gen_ai.response.finish_reasons",
            (Name::AgentName, _) => "gen_ai.agent.name",
            (Name::AgentId, _) => "gen_ai.agent.id",
            (Name::ConversationId, _) => "gen_ai.conversation.id",
            (Name::ToolName, _) => "gen_ai.tool.name",
            (Name::ToolCallId, _) => "gen_ai.tool.call.id",
            (Name::InputMessages, _) => "gen_ai.input.messages",
            (Name::OutputMessages, _) => "gen_ai.output.messages",
        }
    }

    /// Every name → attribute pair, in a fixed order.
    pub fn names(self) -> Vec<(&'static str, &'static str)> {
        use Name::*;
        let all = [
            ("operation", Operation),
            ("provider", Provider),
            ("request_model", RequestModel),
            ("response_model", ResponseModel),
            ("input_tokens", InputTokens),
            ("output_tokens", OutputTokens),
            ("finish_reasons", FinishReasons),
            ("agent_name", AgentName),
            ("agent_id", AgentId),
            ("conversation_id", ConversationId),
            ("tool_name", ToolName),
            ("tool_call_id", ToolCallId),
            ("input_messages", InputMessages),
            ("output_messages", OutputMessages),
        ];
        all.iter()
            .map(|(label, name)| (*label, self.attribute(*name)))
            .collect()
    }
}

/// Chooses the naming generation: an explicit `latest` / `legacy` wins, anything else explicit is
/// an error; otherwise the environment's opt-in (and the default) select the latest names.
pub fn resolve_convention(explicit: Option<&str>) -> Result<Convention, String> {
    match explicit {
        None | Some("latest") => Ok(Convention::Latest),
        Some("legacy") => Ok(Convention::Legacy),
        Some(other) => Err(format!(
            "semconv must be one of latest, legacy, got {}",
            Value::String(other.to_string())
        )),
    }
}

/// Whether the environment value switches on prompt and completion capture.
pub fn capture_content_enabled(value: Option<&str>) -> bool {
    value.is_some_and(|v| v.trim_matches(ASCII_SPACE).eq_ignore_ascii_case("true"))
}

/// How a span is shown to OpenTelemetry.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SpanKind {
    Client,
    Internal,
}

/// A span a record describes: its name, kind, attributes, and how long it lasted.
#[derive(Clone, Debug, PartialEq)]
pub struct SpanSpec {
    pub name: String,
    pub kind: SpanKind,
    pub attributes: Map<String, Value>,
    pub duration_ms: Option<f64>,
    pub is_error: bool,
    pub events: Vec<(String, Map<String, Value>)>,
}

impl SpanSpec {
    /// The span as JSON: name, kind, attributes, duration, error flag and events.
    pub fn to_value(&self) -> Value {
        serde_json::json!({
            "name": self.name,
            "kind": match self.kind { SpanKind::Client => "client", SpanKind::Internal => "internal" },
            "attributes": self.attributes,
            "duration_ms": self.duration_ms.map_or(Value::Null, float_value),
            "is_error": self.is_error,
            "events": self.events.iter().map(|(name, attrs)| serde_json::json!([name, attrs])).collect::<Vec<_>>(),
        })
    }
}

fn string(value: Option<&Value>) -> Option<&str> {
    match value {
        Some(Value::String(s)) if !s.is_empty() => Some(s),
        _ => None,
    }
}

fn number(value: Option<&Value>) -> Option<f64> {
    match value {
        Some(Value::Number(n)) => n.to_string().parse().ok(),
        _ => None,
    }
}

fn empty() -> &'static Map<String, Value> {
    static EMPTY: std::sync::OnceLock<Map<String, Value>> = std::sync::OnceLock::new();
    EMPTY.get_or_init(Map::new)
}

fn meta_of(record: &Value) -> &Map<String, Value> {
    record
        .get("meta")
        .and_then(Value::as_object)
        .unwrap_or_else(|| empty())
}

fn error_type(meta: &Map<String, Value>) -> Option<String> {
    let error = string(meta.get("error"))?;
    let head = error
        .split(':')
        .next()
        .unwrap_or("")
        .trim_matches(ASCII_SPACE);
    Some(if head.is_empty() || head.contains(' ') {
        "_OTHER".to_string()
    } else {
        head.to_string()
    })
}

/// A record `Logger.span()` wrote on exit.
pub fn is_span_record(record: &Value) -> bool {
    let meta = meta_of(record);
    meta.get("kind").and_then(Value::as_str) == Some("span")
        && meta.get("span_id").is_some_and(Value::is_string)
}

/// A record carrying an `llm` block.
pub fn is_llm_record(record: &Value) -> bool {
    record.get("llm").is_some_and(Value::is_object)
}

/// An `.action()` naming the tool it called.
pub fn is_tool_record(record: &Value) -> bool {
    let meta = meta_of(record);
    meta.get("kind").and_then(Value::as_str) == Some("action") && string(meta.get("tool")).is_some()
}

fn put(attributes: &mut Map<String, Value>, key: &str, value: Value) {
    attributes.insert(key.to_string(), value);
}

fn common(convention: Convention, meta: &Map<String, Value>, attributes: &mut Map<String, Value>) {
    if let Some(thread) = string(meta.get("thread_id")) {
        put(
            attributes,
            convention.attribute(Name::ConversationId),
            thread.into(),
        );
    }
    if let Some(agent) = string(meta.get("agent_name")) {
        put(
            attributes,
            convention.attribute(Name::AgentName),
            agent.into(),
        );
    }
    if let Some(run) = string(meta.get("run_id")) {
        put(attributes, "logquill.run_id", run.into());
    }
    if let Some(node) = string(meta.get("node_name")) {
        put(attributes, "logquill.node_name", node.into());
    }
    if let Some(retry) = meta.get("retry_count").filter(|v| is_integer(v)) {
        put(attributes, "logquill.retry_count", retry.clone());
    }
    if let Some(kind) = error_type(meta) {
        put(attributes, "error.type", kind.into());
    }
}

fn events(meta: &Map<String, Value>) -> Vec<(String, Map<String, Value>)> {
    match meta.get("state_diff") {
        Some(diff @ Value::Object(_)) => {
            let mut attrs = Map::new();
            put(&mut attrs, "logquill.state", json_line(diff).into());
            vec![("logquill.state_diff".to_string(), attrs)]
        }
        _ => Vec::new(),
    }
}

fn usage(convention: Convention, llm: &Map<String, Value>, out: &mut Map<String, Value>) {
    if let Some(n) = llm.get("tokens_in").filter(|v| is_integer(v)) {
        put(out, convention.attribute(Name::InputTokens), n.clone());
    }
    if let Some(n) = llm.get("tokens_out").filter(|v| is_integer(v)) {
        put(out, convention.attribute(Name::OutputTokens), n.clone());
    }
    if let Some(reason) = string(llm.get("finish_reason")) {
        put(
            out,
            convention.attribute(Name::FinishReasons),
            Value::Array(vec![reason.into()]),
        );
    }
    if let Some(cost) = number(llm.get("cost_usd")) {
        put(out, "logquill.cost_usd", float_value(cost));
    }
}

/// Options for [`llm_span`] and [`span_spec`].
#[derive(Clone, Debug)]
pub struct SpecOptions {
    /// `gen_ai.provider.name` for records without `meta.provider`.
    pub default_provider: String,
    /// Add prompt and completion text (`meta.input_messages` / `output_messages`).
    pub capture_content: bool,
}

impl Default for SpecOptions {
    fn default() -> Self {
        SpecOptions {
            default_provider: "unknown".to_string(),
            capture_content: false,
        }
    }
}

/// The inference span for a record with an `llm` block.
pub fn llm_span(record: &Value, convention: Convention, options: &SpecOptions) -> SpanSpec {
    let meta = meta_of(record);
    let llm = record
        .get("llm")
        .and_then(Value::as_object)
        .unwrap_or_else(|| empty());
    let operation = if meta.get("operation").and_then(Value::as_str) == Some("text_completion") {
        "text_completion"
    } else {
        "chat"
    };
    let model = string(llm.get("model"));
    let mut attributes = Map::new();
    put(
        &mut attributes,
        convention.attribute(Name::Operation),
        operation.into(),
    );
    let provider = string(meta.get("provider")).unwrap_or(&options.default_provider);
    put(
        &mut attributes,
        convention.attribute(Name::Provider),
        provider.into(),
    );
    if let Some(model) = model {
        put(
            &mut attributes,
            convention.attribute(Name::RequestModel),
            model.into(),
        );
    }
    if let Some(response) = string(meta.get("response_model")) {
        put(
            &mut attributes,
            convention.attribute(Name::ResponseModel),
            response.into(),
        );
    }
    usage(convention, llm, &mut attributes);
    if options.capture_content {
        for (key, name) in [
            ("input_messages", Name::InputMessages),
            ("output_messages", Name::OutputMessages),
        ] {
            if let Some(content) = meta.get(key).filter(|v| !v.is_null()) {
                put(
                    &mut attributes,
                    convention.attribute(name),
                    json_line(content).into(),
                );
            }
        }
    }
    common(convention, meta, &mut attributes);
    let level = record.get("level").and_then(Value::as_str);
    SpanSpec {
        name: model.map_or_else(|| operation.to_string(), |m| format!("{operation} {m}")),
        kind: SpanKind::Client,
        attributes,
        duration_ms: number(llm.get("latency_ms")),
        is_error: error_type(meta).is_some() || matches!(level, Some("ERROR" | "FATAL")),
        events: events(meta),
    }
}

/// The `execute_tool {tool}` span for an action that names its tool.
pub fn tool_span(record: &Value, convention: Convention) -> SpanSpec {
    let meta = meta_of(record);
    let tool = string(meta.get("tool")).unwrap_or("");
    let mut attributes = Map::new();
    put(
        &mut attributes,
        convention.attribute(Name::Operation),
        "execute_tool".into(),
    );
    put(
        &mut attributes,
        convention.attribute(Name::ToolName),
        tool.into(),
    );
    if let Some(id) = string(meta.get("tool_call_id")) {
        put(
            &mut attributes,
            convention.attribute(Name::ToolCallId),
            id.into(),
        );
    }
    common(convention, meta, &mut attributes);
    SpanSpec {
        name: format!("execute_tool {tool}"),
        kind: SpanKind::Internal,
        attributes,
        duration_ms: number(meta.get("duration_ms")),
        is_error: error_type(meta).is_some(),
        events: events(meta),
    }
}

/// The span for a `Logger.span()` record: `invoke_agent {agent}` when it is an agent run,
/// otherwise the span keeps its own name.
pub fn span_spec(record: &Value, convention: Convention, options: &SpecOptions) -> SpanSpec {
    let meta = meta_of(record);
    let mut attributes = Map::new();
    common(convention, meta, &mut attributes);
    let is_agent = meta.get("operation").and_then(Value::as_str) == Some("invoke_agent")
        || string(meta.get("agent_name")).is_some();
    let (name, kind) = if is_agent {
        let logger = record.get("logger").and_then(Value::as_str).unwrap_or("");
        let agent = string(meta.get("agent_name")).unwrap_or(logger);
        put(
            &mut attributes,
            convention.attribute(Name::Operation),
            "invoke_agent".into(),
        );
        let provider = string(meta.get("provider")).unwrap_or(&options.default_provider);
        put(
            &mut attributes,
            convention.attribute(Name::Provider),
            provider.into(),
        );
        put(
            &mut attributes,
            convention.attribute(Name::AgentName),
            agent.into(),
        );
        if let Some(id) = string(meta.get("agent_id")) {
            put(
                &mut attributes,
                convention.attribute(Name::AgentId),
                id.into(),
            );
        }
        (format!("invoke_agent {agent}"), SpanKind::Client)
    } else {
        (
            record
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            SpanKind::Internal,
        )
    };
    let level = record.get("level").and_then(Value::as_str);
    SpanSpec {
        name,
        kind,
        attributes,
        duration_ms: number(meta.get("duration_ms")),
        is_error: matches!(level, Some("ERROR" | "FATAL")),
        events: events(meta),
    }
}

/// The span a record becomes when exported (LLM, then tool, then span), or `None` for any other.
pub fn export_spec(
    record: &Value,
    convention: Convention,
    options: &SpecOptions,
) -> Option<SpanSpec> {
    if is_llm_record(record) {
        Some(llm_span(record, convention, options))
    } else if is_tool_record(record) {
        Some(tool_span(record, convention))
    } else if is_span_record(record) {
        Some(span_spec(record, convention, options))
    } else {
        None
    }
}

/// The GenAI attributes for a *log* record: the `llm` block's usage and model.
pub fn log_attributes(record: &Value, convention: Convention) -> Map<String, Value> {
    let mut out = Map::new();
    let Some(llm) = record.get("llm").and_then(Value::as_object) else {
        return out;
    };
    put(
        &mut out,
        convention.attribute(Name::Operation),
        "chat".into(),
    );
    if let Some(model) = string(llm.get("model")) {
        put(
            &mut out,
            convention.attribute(Name::RequestModel),
            model.into(),
        );
    }
    usage(convention, llm, &mut out);
    out
}

/// A record timestamp (`YYYY-MM-DDTHH:MM:SS[.f…]Z`, up to nine fraction digits) as nanoseconds
/// since the Unix epoch, read exactly; `None` for anything else.
pub fn epoch_ns(timestamp: &str) -> Option<i128> {
    let b = timestamp.as_bytes();
    if b.len() < 20 || !timestamp.is_ascii() || b[b.len() - 1] != b'Z' {
        return None;
    }
    let digits = |from: usize, to: usize| -> Option<i64> {
        let part = timestamp.get(from..to)?;
        part.bytes()
            .all(|c| c.is_ascii_digit())
            .then(|| part.parse().ok())
            .flatten()
    };
    if b[4] != b'-' || b[7] != b'-' || b[10] != b'T' || b[13] != b':' || b[16] != b':' {
        return None;
    }
    let (year, month, day) = (digits(0, 4)?, digits(5, 7)?, digits(8, 10)?);
    let (hour, minute, second) = (digits(11, 13)?, digits(14, 16)?, digits(17, 19)?);
    let fraction = &timestamp[19..timestamp.len() - 1];
    let nanos: i128 = if fraction.is_empty() {
        0
    } else {
        let rest = fraction.strip_prefix('.')?;
        if rest.is_empty() || rest.len() > 9 || !rest.bytes().all(|c| c.is_ascii_digit()) {
            return None;
        }
        format!("{rest:0<9}").parse().ok()?
    };
    let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
    let month_days = [
        31,
        if leap { 29 } else { 28 },
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ];
    if !(1..=12).contains(&month) || day < 1 || day > month_days[(month - 1) as usize] {
        return None;
    }
    if hour > 23 || minute > 59 || second > 59 || year < 1 {
        return None;
    }
    // Days since 1970-01-01 (Howard Hinnant's days_from_civil).
    let y = if month <= 2 { year - 1 } else { year };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let doy = (153 * (if month > 2 { month - 3 } else { month + 9 }) + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    let seconds = days * 86_400 + hour * 3600 + minute * 60 + second;
    Some(i128::from(seconds) * 1_000_000_000 + nanos)
}

/// The Elastic Common Schema document for a record: the record itself with `@timestamp`,
/// `log.level`, `service.name`, `trace.id`, `span.id` and (for a stack) `error.*` added beside
/// LogQuill's own fields. A record with no or null `meta` is mapped, not rejected.
pub fn to_ecs_document(record: &Value) -> Value {
    let mut document = record.as_object().cloned().unwrap_or_default();
    let meta = meta_of(record);
    if let Some(timestamp) = record.get("timestamp") {
        document.insert("@timestamp".into(), timestamp.clone());
    }
    if let Some(level) = record.get("level").and_then(Value::as_str) {
        document.insert(
            "log".into(),
            serde_json::json!({"level": level.to_lowercase()}),
        );
    }
    if let Some(logger) = record.get("logger") {
        document.insert("service".into(), serde_json::json!({"name": logger}));
    }
    if let Some(trace) = meta.get("trace_id").filter(|v| !v.is_null()) {
        document.insert("trace".into(), serde_json::json!({"id": trace}));
    }
    if let Some(span) = meta.get("span_id").filter(|v| !v.is_null()) {
        document.insert("span".into(), serde_json::json!({"id": span}));
    }
    if let Some(Value::String(stack)) = meta.get("stack") {
        let message = record.get("message").cloned().unwrap_or_else(|| "".into());
        document.insert(
            "error".into(),
            serde_json::json!({"message": message, "stack_trace": stack}),
        );
    }
    Value::Object(document)
}
