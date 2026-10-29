//! The stateless helpers behind `lq_call(name, args_json)`, in safe Rust. One function per helper, named as the Node
//! addon names it (`bindings/node/index.d.ts`), taking its arguments as a JSON array and returning a JSON value.
//!
//! An argument that is a record, `meta`, a set of fields or a list of records may be given as a JSON value **or** as a
//! string holding its JSON text. The text form is for hosts that cannot write a number as it was spelled (`1` and `1.0`
//! are different to the tamper chain): they build the text themselves and pass it through untouched. Text results (a
//! formatted line, canonical JSON) come back as JSON strings; structured results come back as JSON values.

use base64::{engine::general_purpose::STANDARD, Engine};
use logquill_core as core;
use logquill_core::Clock;
use serde_json::{json, Map, Value};

type Res = Result<Value, String>;

struct Args<'a>(&'a [Value]);

impl Args<'_> {
    fn get(&self, i: usize) -> Option<&Value> {
        self.0.get(i).filter(|v| !v.is_null())
    }

    fn text(&self, i: usize, what: &str) -> Result<&str, String> {
        match self.get(i) {
            Some(Value::String(s)) => Ok(s),
            Some(_) => Err(format!("{what} (argument {i}) must be a string")),
            None => Err(format!("{what} (argument {i}) is missing")),
        }
    }

    fn opt_text(&self, i: usize, what: &str) -> Result<Option<&str>, String> {
        match self.get(i) {
            None => Ok(None),
            Some(Value::String(s)) => Ok(Some(s)),
            Some(_) => Err(format!("{what} (argument {i}) must be a string")),
        }
    }

    fn number(&self, i: usize, what: &str) -> Result<f64, String> {
        self.get(i)
            .and_then(Value::as_f64)
            .ok_or_else(|| format!("{what} (argument {i}) must be a number"))
    }

    fn opt_bool(&self, i: usize) -> Option<bool> {
        self.get(i).and_then(Value::as_bool)
    }

    /// A JSON value, or the value a string argument holds as JSON text.
    fn json(&self, i: usize, what: &str) -> Result<Value, String> {
        match self.get(i) {
            None => Err(format!("{what} (argument {i}) is missing")),
            Some(Value::String(text)) => core::parse_lenient(text).map_err(|e| {
                format!(
                    "{what} is not valid JSON ({e}) \u{2014} pass the output of your JSON encoder"
                )
            }),
            Some(value) => Ok(value.clone()),
        }
    }

    fn object(&self, i: usize, what: &str) -> Result<Map<String, Value>, String> {
        match self.json(i, what)? {
            Value::Object(map) => Ok(map),
            _ => Err(format!("{what} must be a JSON object")),
        }
    }

    fn records(&self, i: usize) -> Result<Vec<Value>, String> {
        match self.json(i, "the records")? {
            Value::Array(items) => Ok(items),
            _ => Err("the records must be a JSON array of record objects".into()),
        }
    }

    /// A key given as base64.
    fn key(&self, i: usize) -> Result<Vec<u8>, String> {
        STANDARD
            .decode(self.text(i, "the key")?)
            .map_err(|e| format!("the key is not valid base64 ({e})"))
    }
}

fn field_class(name: &str) -> Result<core::FieldClass, String> {
    match name {
        "secret" => Ok(core::FieldClass::Secret),
        "pii" => Ok(core::FieldClass::Pii),
        "content" => Ok(core::FieldClass::Content),
        other => Err(format!(
            "a field class is 'secret', 'pii' or 'content', got '{other}'"
        )),
    }
}

fn verification(result: core::Verification) -> Value {
    let mut out = Map::new();
    out.insert("ok".into(), Value::Bool(result.ok));
    out.insert("recordsChecked".into(), result.records_checked.into());
    if let Some(n) = result.broken_at {
        out.insert("brokenAt".into(), n.into());
    }
    if let Some(reason) = result.reason {
        out.insert("reason".into(), reason.into());
    }
    if let Some(head) = result.head_hash {
        out.insert("headHash".into(), head.into());
    }
    Value::Object(out)
}

fn convention(name: Option<&str>) -> Result<core::Convention, String> {
    core::resolve_convention(name)
}

/// Builds the `KeyRedactor` an options object describes: `{keys?, classes?, replacement?}`.
pub(crate) fn key_redactor(
    options: Option<&Map<String, Value>>,
) -> Result<core::KeyRedactor, String> {
    let empty = Map::new();
    let options = options.unwrap_or(&empty);
    let strings = |key: &str| -> Option<Vec<String>> {
        options.get(key)?.as_array().map(|list| {
            list.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
    };
    let keys = strings("keys").unwrap_or_else(|| {
        core::DEFAULT_KEYS
            .iter()
            .map(|k| (*k).to_string())
            .collect()
    });
    let classes = strings("classes")
        .unwrap_or_default()
        .iter()
        .map(|name| field_class(name))
        .collect::<Result<Vec<_>, _>>()?;
    let replacement = options
        .get("replacement")
        .and_then(Value::as_str)
        .unwrap_or("***");
    Ok(core::KeyRedactor::new(
        keys.iter().map(String::as_str),
        &classes,
        replacement,
    ))
}

/// Runs the helper `name` with `args`.
pub fn call(name: &str, args: &[Value]) -> Res {
    let a = Args(args);
    match name {
        // ---- text and formatting ----------------------------------------------------------------------------
        "canonicalJson" => core::Canon::parse(a.text(0, "the JSON text")?)
            .map(|value| Value::String(value.to_text()))
            .map_err(|e| format!("the value is not valid JSON ({e})")),
        "formatJson" => Ok(core::format_json(&a.json(0, "the record")?).into()),
        "formatText" => Ok(core::format_text(&a.json(0, "the record")?).into()),
        "formatLogfmt" => Ok(core::format_logfmt(&a.json(0, "the record")?).into()),
        "parseLogfmt" => Ok(Value::Object(core::parse_logfmt(a.text(0, "the line")?))),
        "parseRecord" => core::parse_record(&a.json(0, "the record")?)
            .map(|record| record.to_value())
            .map_err(|e| e.to_string()),

        // ---- integrity --------------------------------------------------------------------------------------
        "genesisHash" => Ok(core::GENESIS_HASH.into()),
        "verifyChain" => {
            let records = a.records(0)?;
            let genesis = a
                .opt_text(1, "the genesis hash")?
                .unwrap_or(core::GENESIS_HASH);
            Ok(verification(core::verify_chain(records.iter(), genesis)))
        }
        "signHead" => Ok(core::sign_head(a.text(0, "the head hash")?, &a.key(1)?).into()),
        "verifyHeadSignature" => Ok(core::verify_head_signature(
            a.text(0, "the head hash")?,
            &a.key(1)?,
            a.text(2, "the signature")?,
        )
        .into()),
        "verifySignedChain" => {
            let records = a.records(0)?;
            let genesis = a
                .opt_text(3, "the genesis hash")?
                .unwrap_or(core::GENESIS_HASH);
            Ok(verification(core::verify_signed_chain(
                records.iter(),
                &a.key(1)?,
                a.text(2, "the signature")?,
                genesis,
            )))
        }

        // ---- redaction and privacy ----------------------------------------------------------------------------
        "redactMeta" => {
            let mut meta = a.object(0, "the meta")?;
            let options = if a.get(1).is_some() {
                Some(a.object(1, "the options")?)
            } else {
                None
            };
            key_redactor(options.as_ref())?.redact_meta(&mut meta);
            Ok(Value::Object(meta))
        }
        "redactLocal" => {
            let options = if a.get(2).is_some() {
                Some(a.object(2, "the options")?)
            } else {
                None
            };
            Ok(key_redactor(options.as_ref())?
                .redact_local(a.text(0, "the name")?, a.text(1, "the text")?)
                .into())
        }
        "piiRedactMeta" => {
            let mut meta = a.object(0, "the meta")?;
            core::PiiRedactor::new(a.opt_text(1, "the replacement")?.unwrap_or("***"))
                .redact_meta(&mut meta);
            Ok(Value::Object(meta))
        }
        "piiRedactText" => Ok(core::PiiRedactor::new(
            a.opt_text(1, "the replacement")?.unwrap_or("***"),
        )
        .redact_text(a.text(0, "the text")?)
        .into()),
        "applyPolicyValue" => {
            let policy =
                core::ContentPolicy::parse(a.text(1, "the policy")?).map_err(|e| e.to_string())?;
            Ok(policy.apply(&a.json(0, "the value")?))
        }
        "applyContentPolicy" => {
            let policy =
                core::ContentPolicy::parse(a.text(1, "the policy")?).map_err(|e| e.to_string())?;
            let mut meta = a.object(0, "the meta")?;
            core::apply_content_policy(&mut meta, policy);
            Ok(Value::Object(meta))
        }
        "contentFields" => Ok(core::CONTENT_FIELDS.iter().map(|k| json!(k)).collect()),
        "fieldClasses" => Ok(core::FIELD_CLASSES
            .iter()
            .map(|(key, class)| json!({"key": key, "class": class.name()}))
            .collect()),
        "keysInClass" => Ok(core::keys_in_class(field_class(a.text(0, "the class")?)?)
            .into_iter()
            .map(|k| json!(k))
            .collect()),

        // ---- rows for storage ---------------------------------------------------------------------------------
        "toEcsDocument" => Ok(core::to_ecs_document(&a.json(0, "the record")?)),
        "sqlRow" => Ok(core::sql_row(&a.json(0, "the record")?)),
        "createTableSql" => {
            let dialect = match a.text(0, "the dialect")? {
                "sqlite" => core::Dialect::Sqlite,
                "postgres" => core::Dialect::Postgres,
                "mysql" => core::Dialect::Mysql,
                other => {
                    return Err(format!(
                        "dialect must be 'sqlite', 'postgres' or 'mysql', got '{other}'"
                    ))
                }
            };
            Ok(core::create_table_sql(dialect, a.text(1, "the table name")?).into())
        }
        "graphRow" => {
            let keys = core::GraphKeys {
                run_key: a
                    .opt_text(1, "the run key")?
                    .unwrap_or("run_id")
                    .to_string(),
                trace_key: a
                    .opt_text(2, "the trace key")?
                    .unwrap_or("trace_id")
                    .to_string(),
            };
            Ok(core::graph_row(&a.json(0, "the record")?, &keys))
        }
        "cypher" => Ok(core::CYPHER.into()),
        "leafPlaceholder" => Ok(core::LEAF_PLACEHOLDER.into()),

        // ---- levels, time, the llm block ------------------------------------------------------------------------
        "levelFromJson" => core::Level::from_json(&a.json(0, "the level")?)
            .map(|level| level.name().into())
            .map_err(|e| e.to_string()),
        "utcTimestamp" => Ok(core::format_timestamp(core::SystemClock.now_unix_micros()).into()),
        "formatTimestamp" => {
            Ok(core::format_timestamp(a.number(0, "the microseconds")? as i64).into())
        }
        "buildLlmBlock" => {
            let built = core::build_llm_block(&a.object(0, "the fields")?);
            Ok(json!({"block": built.block.map(Value::Object), "warnings": built.warnings}))
        }

        // ---- trace and OpenTelemetry ids ------------------------------------------------------------------------
        "parseTraceHeader" => Ok(core::parse_trace_header(a.text(0, "the header")?).into()),
        "spanIdFor" => Ok(core::span_id_for(a.text(0, "the value")?).into()),
        "traceIdFor" => Ok(core::trace_id_for(&a.object(0, "the meta")?).into()),
        "traceIdFromParent" => {
            Ok(core::trace_id_from_parent(a.text(0, "the parent span id")?).into())
        }

        // ---- GenAI attribute mapping ----------------------------------------------------------------------------
        "captureEnv" => Ok(core::CAPTURE_ENV.into()),
        "captureContentEnabled" => {
            Ok(core::capture_content_enabled(a.opt_text(0, "the value")?).into())
        }
        "semconvResolve" => {
            let convention = convention(a.opt_text(0, "the convention")?)?;
            let names: Map<String, Value> = convention
                .names()
                .into_iter()
                .map(|(key, value)| (key.to_string(), json!(value)))
                .collect();
            Ok(json!({"version": convention.version(), "names": names}))
        }
        "epochNs" => Ok(core::epoch_ns(a.text(0, "the timestamp")?)
            .map(|ns| ns.to_string())
            .into()),
        "classifyRecord" => {
            let record = a.json(0, "the record")?;
            Ok(json!({
                "span": core::is_span_record(&record),
                "llm": core::is_llm_record(&record),
                "tool": core::is_tool_record(&record),
            }))
        }
        "logAttributes" => {
            let convention =
                convention(Some(a.opt_text(1, "the convention")?.unwrap_or("latest")))?;
            Ok(Value::Object(core::log_attributes(
                &a.json(0, "the record")?,
                convention,
            )))
        }
        "exportSpec" => {
            let convention =
                convention(Some(a.opt_text(1, "the convention")?.unwrap_or("latest")))?;
            let defaults = core::SpecOptions::default();
            let options = core::SpecOptions {
                default_provider: a
                    .opt_text(2, "the default provider")?
                    .map_or(defaults.default_provider, str::to_string),
                capture_content: a.opt_bool(3).unwrap_or(false),
            };
            Ok(
                core::export_spec(&a.json(0, "the record")?, convention, &options)
                    .map(|spec| spec.to_value())
                    .into(),
            )
        }

        // ---- trace trees ----------------------------------------------------------------------------------------
        "buildTrace" => {
            let records = a.records(0)?;
            let trace = core::build_trace(records.iter(), a.text(1, "the run id")?);
            Ok(json!({
                "record_count": trace.record_count,
                "roots": trace.roots.iter().map(core::TreeNode::to_value).collect::<Vec<_>>(),
                "text": core::render_tree(&trace.roots),
            }))
        }

        other => Err(format!(
            "unknown helper '{other}' \u{2014} the helpers are listed in docs/stages/stage-8.md"
        )),
    }
}
