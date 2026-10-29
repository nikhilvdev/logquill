//! The `Logger` behind the C handle, in safe Rust: the core's pipeline (level, content policy, redaction) in front of the
//! runtime's worker and transports. Built from a JSON config, so a host passes one string and never builds a plugin object
//! across the ABI:
//!
//! ```json
//! {"name": "app", "level": "INFO", "content_policy": "off",
//!  "plugins": [{"type": "redact", "keys": ["password"], "classes": ["secret"], "replacement": "***"},
//!              {"type": "pii", "replacement": "***"},
//!              {"type": "sampling", "rate": 0.5},
//!              {"type": "rate_limit", "max_records": 100, "per_seconds": 1, "key": "logger_level"},
//!              {"type": "adaptive_sampling", "base_rate": 0.1, "max_bytes_per_second": 100000}],
//!  "transports": [{"type": "console", "format": "json", "colour": false},
//!                 {"type": "file", "path": "app.log", "format": "json", "max_bytes": 10485760, "backup_count": 5}],
//!  "tamper_evident": false,
//!  "queue": {"max_size": 10000, "backpressure": "drop_oldest"}}
//! ```
//!
//! Every key is optional. An unknown key is an error, not ignored: a misspelt `plugins` entry that quietly did nothing
//! would leave secrets in the log. The hash chain (`tamper_evident`) is applied as the Rust transports write each
//! record; the copy handed back to the host is not chained.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use logquill_core as core;
#[cfg(feature = "runtime")]
use logquill_core::SharedChain;
use logquill_core::{Call, Level, Pipeline, SystemClock};
use serde_json::{Map, Value};

#[cfg(feature = "runtime")]
use logquill_runtime as rt;

/// What a log call produced.
#[derive(Debug, PartialEq, Eq)]
pub enum Logged {
    /// The finished record, as JSON text. It has also been queued to the logger's own transports, if it has any.
    Kept(String),
    /// Filtered out: below the level, switched off, or dropped by a plugin.
    Dropped,
}

struct Shared {
    pipeline: Mutex<Pipeline>,
    #[cfg(feature = "runtime")]
    chain: Option<Arc<SharedChain>>,
    #[cfg(feature = "runtime")]
    dispatcher: Option<rt::Dispatcher>,
}

impl Drop for Shared {
    fn drop(&mut self) {
        // the last handle went away without `close`: do not lose what is queued
        #[cfg(feature = "runtime")]
        if let Some(dispatcher) = &self.dispatcher {
            dispatcher.close(Some(Duration::from_secs(5)));
        }
    }
}

/// A logger, or a child of one: children share the parent's pipeline and transports.
pub struct Logger {
    shared: Arc<Shared>,
    name: String,
    fixed: Map<String, Value>,
}

pub(crate) fn parse_level(name: &str) -> Result<Level, String> {
    Level::from_record_name(&name.to_ascii_uppercase())
        .ok_or_else(|| format!("Unknown log level: {name}"))
}

fn object(text: &str, what: &str) -> Result<Map<String, Value>, String> {
    match core::parse_lenient(text) {
        Ok(Value::Object(map)) => Ok(map),
        Ok(_) => Err(format!("{what} must be a JSON object")),
        Err(e) => Err(format!(
            "{what} is not valid JSON ({e}) \u{2014} pass the output of your JSON encoder"
        )),
    }
}

/// Rejects keys outside `allowed`, naming them and what is allowed.
pub(crate) fn only(map: &Map<String, Value>, allowed: &[&str], what: &str) -> Result<(), String> {
    match map.keys().find(|k| !allowed.contains(&k.as_str())) {
        Some(key) => Err(format!(
            "{what}: unknown option '{key}' \u{2014} the options are: {}",
            allowed.join(", ")
        )),
        None => Ok(()),
    }
}

pub(crate) fn string_of<'a>(
    map: &'a Map<String, Value>,
    key: &str,
    what: &str,
) -> Result<Option<&'a str>, String> {
    match map.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) => Ok(Some(s)),
        Some(_) => Err(format!("{what}: '{key}' must be a string")),
    }
}

#[cfg(feature = "runtime")]
fn number_of(map: &Map<String, Value>, key: &str, what: &str) -> Result<Option<u64>, String> {
    match map.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(v) => v
            .as_u64()
            .map(Some)
            .ok_or_else(|| format!("{what}: '{key}' must be a non-negative integer")),
    }
}

fn bool_of(map: &Map<String, Value>, key: &str, what: &str) -> Result<Option<bool>, String> {
    match map.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Bool(b)) => Ok(Some(*b)),
        Some(_) => Err(format!("{what}: '{key}' must be true or false")),
    }
}

fn plugin(pipeline: Pipeline, spec: &Value) -> Result<Pipeline, String> {
    let Value::Object(spec) = spec else {
        return Err("each plugin must be an object with a \"type\"".into());
    };
    match string_of(spec, "type", "a plugin")? {
        Some("redact") => {
            only(
                spec,
                &["type", "keys", "classes", "replacement"],
                "the redact plugin",
            )?;
            Ok(pipeline.with(crate::call::key_redactor(Some(spec))?))
        }
        Some("pii") => {
            only(spec, &["type", "replacement"], "the pii plugin")?;
            let replacement = string_of(spec, "replacement", "the pii plugin")?.unwrap_or("***");
            Ok(pipeline.with(core::PiiRedactor::new(replacement)))
        }
        Some("sampling") => Ok(pipeline.with(crate::stateful::sampling(spec)?)),
        Some("rate_limit") => Ok(pipeline.with(crate::stateful::rate_limit(spec)?)),
        Some("adaptive_sampling") => Ok(pipeline.with(crate::stateful::adaptive(spec)?)),
        Some(other) => Err(format!(
            "unknown plugin type '{other}' \u{2014} the plugin types are: redact, pii, sampling, rate_limit, adaptive_sampling"
        )),
        None => Err("a plugin needs a \"type\": redact, pii, sampling, rate_limit or adaptive_sampling".into()),
    }
}

#[cfg(feature = "runtime")]
fn transport(spec: &Value) -> Result<Box<dyn rt::Transport>, String> {
    let Value::Object(spec) = spec else {
        return Err("each transport must be an object with a \"type\"".into());
    };
    let formatter = |what: &str| -> Result<rt::Formatter, String> {
        rt::Formatter::parse(string_of(spec, "format", what)?.unwrap_or("json"))
    };
    match string_of(spec, "type", "a transport")? {
        Some("console") => {
            only(spec, &["type", "format", "colour"], "the console transport")?;
            let colour = bool_of(spec, "colour", "the console transport")?
                .unwrap_or_else(rt::colour_default);
            Ok(Box::new(rt::Console::stdio(
                colour,
                formatter("the console transport")?,
            )))
        }
        Some("file") => {
            only(
                spec,
                &[
                    "type",
                    "path",
                    "format",
                    "max_bytes",
                    "backup_count",
                    "compression",
                    "encrypt_key",
                ],
                "the file transport",
            )?;
            let what = "the file transport";
            let path =
                string_of(spec, "path", what)?.ok_or("the file transport needs a \"path\"")?;
            let defaults = rt::FileOptions::default();
            let options = rt::FileOptions {
                max_bytes: number_of(spec, "max_bytes", what)?.unwrap_or(defaults.max_bytes),
                backup_count: number_of(spec, "backup_count", what)?
                    .map_or(defaults.backup_count, |n| n as usize),
                formatter: formatter(what)?,
                compression: match string_of(spec, "compression", what)? {
                    None => None,
                    Some("gz") => Some(rt::Compression::Gz),
                    Some("zip") => Some(rt::Compression::Zip),
                    Some(other) => {
                        return Err(format!(
                            "{what}: compression is 'gz' or 'zip', got '{other}'"
                        ))
                    }
                },
                encrypt_key: string_of(spec, "encrypt_key", what)?.map(str::to_string),
            };
            Ok(Box::new(rt::FileTransport::new(path, options)?))
        }
        Some(other) => Err(format!(
            "unknown transport type '{other}' \u{2014} the transport types are: console, file"
        )),
        None => Err("a transport needs a \"type\": console or file".into()),
    }
}

impl Logger {
    /// Builds a logger from the JSON config described in the module documentation.
    pub fn from_config(text: &str) -> Result<Logger, String> {
        let config = object(text, "the config")?;
        only(
            &config,
            &[
                "name",
                "level",
                "content_policy",
                "plugins",
                "transports",
                "tamper_evident",
                "queue",
            ],
            "the config",
        )?;
        let level = parse_level(string_of(&config, "level", "the config")?.unwrap_or("INFO"))?;
        let mut pipeline = Pipeline::new(level);
        if let Some(policy) = string_of(&config, "content_policy", "the config")? {
            pipeline.content_policy =
                core::ContentPolicy::parse(policy).map_err(|e| e.to_string())?;
        }
        match config.get("plugins") {
            None | Some(Value::Null) => {}
            Some(Value::Array(list)) => {
                for spec in list {
                    pipeline = plugin(pipeline, spec)?;
                }
            }
            Some(_) => return Err("the config: 'plugins' must be an array".into()),
        }

        let specs: &[Value] = match config.get("transports") {
            None | Some(Value::Null) => &[],
            Some(Value::Array(list)) => list,
            Some(_) => return Err("the config: 'transports' must be an array".into()),
        };
        let tamper = bool_of(&config, "tamper_evident", "the config")?.unwrap_or(false);
        if tamper && specs.is_empty() {
            return Err(
                "the config: tamper_evident chains records as the Rust transports write them, so it needs at least one transport"
                    .into(),
            );
        }
        #[cfg(feature = "runtime")]
        let chain = tamper.then(|| Arc::new(SharedChain::new(core::Chain::default())));

        #[cfg(feature = "runtime")]
        let dispatcher = if specs.is_empty() {
            None
        } else {
            let queue = match config.get("queue") {
                None | Some(Value::Null) => Map::new(),
                Some(Value::Object(map)) => map.clone(),
                Some(_) => return Err("the config: 'queue' must be an object".into()),
            };
            only(&queue, &["max_size", "backpressure"], "the queue")?;
            let mut options = rt::WorkerOptions {
                warn: Arc::new(|message: &str| eprintln!("logquill: {message}")),
                ..rt::WorkerOptions::default()
            };
            if let Some(n) = number_of(&queue, "max_size", "the queue")? {
                options.max_queue_size = (n as usize).max(1);
            }
            options.backpressure = match string_of(&queue, "backpressure", "the queue")? {
                None | Some("drop_oldest") => rt::Backpressure::DropOldest,
                Some("drop_newest") => rt::Backpressure::DropNewest,
                Some("block") => rt::Backpressure::Block,
                Some(other) => {
                    return Err(format!("the queue: backpressure is 'drop_oldest', 'drop_newest' or 'block', got '{other}'"))
                }
            };
            let transports = specs.iter().map(transport).collect::<Result<Vec<_>, _>>()?;
            Some(rt::Dispatcher::new(options, transports)?)
        };
        #[cfg(not(feature = "runtime"))]
        if !specs.is_empty() {
            return Err("the config: transports need the runtime, which this build does not include \u{2014} write the returned records yourself".into());
        }

        Ok(Logger {
            shared: Arc::new(Shared {
                pipeline: Mutex::new(pipeline),
                #[cfg(feature = "runtime")]
                chain,
                #[cfg(feature = "runtime")]
                dispatcher,
            }),
            name: string_of(&config, "name", "the config")?
                .unwrap_or("app")
                .to_string(),
            fixed: Map::new(),
        })
    }

    /// This logger's name.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// One log call. `logger` overrides the name for this record; `meta` and `llm` are JSON object text.
    pub fn log(
        &self,
        level: &str,
        logger: Option<&str>,
        message: &str,
        meta: Option<&str>,
        llm: Option<&str>,
    ) -> Result<Logged, String> {
        let level = parse_level(level)?;
        let mut merged = self.fixed.clone();
        if let Some(text) = meta {
            merged.extend(object(text, "the meta")?);
        }
        let llm = llm.map(|text| object(text, "the llm block")).transpose()?;
        let call = Call {
            level,
            logger: logger.unwrap_or(&self.name),
            message,
            meta: merged,
            llm,
            span_id: None,
        };
        let outcome = self
            .shared
            .pipeline
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .process(call, &SystemClock);
        for warning in &outcome.warnings {
            eprintln!("logquill: {warning}");
        }
        match outcome.record {
            Err(_) => Ok(Logged::Dropped),
            Ok(record) => {
                let text = record.to_string();
                #[cfg(feature = "runtime")]
                if let Some(dispatcher) = &self.shared.dispatcher {
                    dispatcher.emit_then(record, self.shared.chain.clone(), |_| {});
                }
                Ok(Logged::Kept(text))
            }
        }
    }

    /// A logger named `<this name>.<suffix>` with `fixed_meta` added to every record, sharing this logger's pipeline,
    /// level and transports.
    pub fn child(&self, suffix: &str, fixed_meta: Option<&str>) -> Result<Logger, String> {
        let mut fixed = self.fixed.clone();
        if let Some(text) = fixed_meta {
            fixed.extend(object(text, "the fixed meta")?);
        }
        Ok(Logger {
            shared: Arc::clone(&self.shared),
            name: format!("{}.{suffix}", self.name),
            fixed,
        })
    }

    /// Changes the level for this logger and everything sharing its pipeline (its parent and children).
    pub fn set_level(&self, level: &str) -> Result<(), String> {
        let level = parse_level(level)?;
        self.shared
            .pipeline
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .level = level;
        Ok(())
    }

    /// Waits for queued records to be written. `false` if `timeout` ran out first.
    pub fn flush(&self, timeout: Duration) -> bool {
        #[cfg(feature = "runtime")]
        if let Some(dispatcher) = &self.shared.dispatcher {
            return dispatcher.flush(Some(timeout));
        }
        let _ = timeout;
        true
    }

    /// Flushes and stops the worker; records logged afterwards are returned to the host but not written.
    pub fn close(&self, timeout: Duration) -> bool {
        #[cfg(feature = "runtime")]
        if let Some(dispatcher) = &self.shared.dispatcher {
            return dispatcher.close(Some(timeout));
        }
        let _ = timeout;
        true
    }
}
