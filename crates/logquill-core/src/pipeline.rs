//! The pipeline a log call passes through before anything is written: level filter, per-logger
//! toggle, record building, retry tracking, content policy, then the plugins in order
//! (`docs/spec/01-records-and-logger.md`, `docs/spec/02-plugins.md`; decisions D20, D22).
//!
//! The core has no logger object, clock source of its own or threads: the caller passes the clock
//! and calls [`Pipeline::process`]. A plugin that fails is skipped, except one that
//! [`fails closed`](Plugin::fails_closed) (a redactor), which drops the record instead of letting
//! it through unredacted.

use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::Mutex;

use serde_json::{Map, Value};

use crate::chain::Chain;
use crate::policy::{apply_content_policy, ContentPolicy};
use crate::record::Record;
use crate::redact::{KeyRedactor, PiiRedactor};
use crate::retry::RetryTracker;
use crate::time::Clock;
use crate::toggle::Toggle;
use crate::Level;

/// What a plugin decided about a record.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Verdict {
    /// Pass it on (possibly changed).
    Keep,
    /// Drop it quietly.
    Drop,
}

/// One step of the pipeline. It may change the record in place.
pub trait Plugin {
    /// A name for messages.
    fn name(&self) -> &str;

    /// Looks at, changes or drops a record. An `Err` is a failure of the plugin itself.
    fn before_log(&mut self, record: &mut Value) -> Result<Verdict, String>;

    /// Whether a failure of this plugin must drop the record (true for redactors).
    fn fails_closed(&self) -> bool {
        false
    }
}

/// A plugin written as a closure.
pub struct FnPlugin<F> {
    name: String,
    fail_closed: bool,
    run: F,
}

impl<F: FnMut(&mut Value) -> Result<Verdict, String>> FnPlugin<F> {
    /// A plugin called `name`; set `fail_closed` for anything that protects data.
    pub fn new(name: &str, fail_closed: bool, run: F) -> FnPlugin<F> {
        FnPlugin {
            name: name.to_string(),
            fail_closed,
            run,
        }
    }
}

impl<F: FnMut(&mut Value) -> Result<Verdict, String>> Plugin for FnPlugin<F> {
    fn name(&self) -> &str {
        &self.name
    }

    fn before_log(&mut self, record: &mut Value) -> Result<Verdict, String> {
        (self.run)(record)
    }

    fn fails_closed(&self) -> bool {
        self.fail_closed
    }
}

fn meta_mut(record: &mut Value) -> Option<&mut Map<String, Value>> {
    record.get_mut("meta").and_then(Value::as_object_mut)
}

impl Plugin for KeyRedactor {
    fn name(&self) -> &str {
        "redact"
    }

    fn before_log(&mut self, record: &mut Value) -> Result<Verdict, String> {
        if let Some(meta) = meta_mut(record) {
            self.redact_meta(meta);
        }
        Ok(Verdict::Keep)
    }

    fn fails_closed(&self) -> bool {
        true
    }
}

impl Plugin for PiiRedactor {
    fn name(&self) -> &str {
        "pii_redact"
    }

    fn before_log(&mut self, record: &mut Value) -> Result<Verdict, String> {
        if let Some(meta) = meta_mut(record) {
            self.redact_meta(meta);
        }
        Ok(Verdict::Keep)
    }

    fn fails_closed(&self) -> bool {
        true
    }
}

/// Why a record did not come out of the pipeline.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Dropped {
    /// Below the logger's level.
    BelowLevel,
    /// The logger (or a parent) is switched off.
    Disabled,
    /// A plugin dropped it.
    ByPlugin(String),
    /// A plugin that protects data failed, so the record was withheld.
    PluginFailed { plugin: String, error: String },
}

/// The result of one log call.
#[derive(Clone, Debug, PartialEq)]
pub struct Outcome {
    /// The finished record, or why there is none.
    pub record: Result<Value, Dropped>,
    /// Failures of plugins that were skipped, for the caller to report.
    pub warnings: Vec<String>,
}

/// One log call's inputs besides the clock.
pub struct Call<'a> {
    pub level: Level,
    pub logger: &'a str,
    pub message: &'a str,
    pub meta: Map<String, Value>,
    pub llm: Option<Map<String, Value>>,
    /// The id of the span the call is made inside, for retry tracking.
    pub span_id: Option<&'a str>,
}

/// Level, toggle, content policy, retry tracking and plugins, applied in that order.
pub struct Pipeline {
    pub level: Level,
    pub toggle: Toggle,
    pub content_policy: ContentPolicy,
    pub retries: RetryTracker,
    plugins: Vec<Box<dyn Plugin + Send>>,
}

impl Pipeline {
    /// A pipeline at `level` with the default (`off`) content policy and no plugins.
    pub fn new(level: Level) -> Pipeline {
        Pipeline {
            level,
            toggle: Toggle::new(),
            content_policy: ContentPolicy::Off,
            retries: RetryTracker::default(),
            plugins: Vec::new(),
        }
    }

    /// Appends a plugin; plugins run in the order they were added.
    pub fn with(mut self, plugin: impl Plugin + Send + 'static) -> Pipeline {
        self.plugins.push(Box::new(plugin));
        self
    }

    fn track_retries(&mut self, call: &Call, meta: &mut Map<String, Value>) {
        let Some(tool) = meta
            .get("tool")
            .and_then(Value::as_str)
            .filter(|t| !t.is_empty())
            .map(str::to_string)
        else {
            return;
        };
        let call_id = meta
            .get("tool_call_id")
            .and_then(Value::as_str)
            .map(str::to_string);
        match meta.get("kind").and_then(Value::as_str) {
            Some("action") => {
                let count = self.retries.opened(call.span_id, &tool, call_id.as_deref());
                if count > 0 && !meta.contains_key("retry_count") {
                    meta.insert("retry_count".into(), Value::from(count));
                }
            }
            Some("observation") if call.level < Level::Error && !meta.contains_key("error") => {
                self.retries
                    .succeeded(call.span_id, &tool, call_id.as_deref());
            }
            _ => {}
        }
    }

    /// Runs one log call. `clock` stamps the record.
    pub fn process(&mut self, call: Call, clock: &dyn Clock) -> Outcome {
        let mut warnings = Vec::new();
        if call.level < self.level {
            return Outcome {
                record: Err(Dropped::BelowLevel),
                warnings,
            };
        }
        if !self.toggle.is_enabled(call.logger) {
            return Outcome {
                record: Err(Dropped::Disabled),
                warnings,
            };
        }
        let mut meta = call.meta.clone();
        self.track_retries(&call, &mut meta);
        apply_content_policy(&mut meta, self.content_policy);
        let mut record = Record::new(
            clock,
            call.level,
            call.logger,
            call.message,
            meta,
            call.llm.clone(),
        )
        .to_value();
        for plugin in &mut self.plugins {
            let run = catch_unwind(AssertUnwindSafe(|| plugin.before_log(&mut record)));
            let failure = match run {
                Ok(Ok(Verdict::Keep)) => continue,
                Ok(Ok(Verdict::Drop)) => {
                    return Outcome {
                        record: Err(Dropped::ByPlugin(plugin.name().to_string())),
                        warnings,
                    };
                }
                Ok(Err(error)) => error,
                Err(_) => "the plugin panicked".to_string(),
            };
            if plugin.fails_closed() {
                let dropped = Dropped::PluginFailed {
                    plugin: plugin.name().to_string(),
                    error: failure,
                };
                return Outcome {
                    record: Err(dropped),
                    warnings,
                };
            }
            warnings.push(format!(
                "plugin {} failed and was skipped: {failure}",
                plugin.name()
            ));
        }
        Outcome {
            record: Ok(record),
            warnings,
        }
    }
}

/// A hash chain several writers share. Each record is chained and handed to the sink inside one
/// critical section, so chain order is write order however many threads log (decision D22).
#[derive(Debug, Default)]
pub struct SharedChain {
    chain: Mutex<Chain>,
}

impl SharedChain {
    pub fn new(chain: Chain) -> SharedChain {
        SharedChain {
            chain: Mutex::new(chain),
        }
    }

    /// Chains `record`, then calls `write` with it, all while holding the lock.
    pub fn write<R>(&self, mut record: Value, write: impl FnOnce(&Value) -> R) -> R {
        let mut chain = self
            .chain
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        chain.stamp(&mut record);
        write(&record)
    }

    /// The hash of the last record written.
    pub fn head(&self) -> String {
        self.chain
            .lock()
            .unwrap_or_else(|p| p.into_inner())
            .head()
            .to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    struct Fixed;

    impl Clock for Fixed {
        fn now_unix_micros(&self) -> i64 {
            1_700_000_000_000_000
        }
    }

    fn call<'a>(level: Level, logger: &'a str, meta: Value) -> Call<'a> {
        Call {
            level,
            logger,
            message: "m",
            meta: meta.as_object().cloned().unwrap_or_default(),
            llm: None,
            span_id: None,
        }
    }

    #[test]
    fn level_and_toggle_filter_before_anything_else() {
        let mut pipeline = Pipeline::new(Level::Warn);
        assert_eq!(
            pipeline
                .process(call(Level::Info, "a", json!({})), &Fixed)
                .record,
            Err(Dropped::BelowLevel)
        );
        pipeline.toggle.disable("a");
        assert_eq!(
            pipeline
                .process(call(Level::Error, "a.b", json!({})), &Fixed)
                .record,
            Err(Dropped::Disabled)
        );
        assert!(pipeline
            .process(call(Level::Error, "other", json!({})), &Fixed)
            .record
            .is_ok());
    }

    #[test]
    fn content_is_hidden_before_a_plugin_sees_it() {
        let seen = std::sync::Arc::new(Mutex::new(Value::Null));
        let sink = seen.clone();
        let mut pipeline = Pipeline::new(Level::Info).with(FnPlugin::new("spy", false, move |r| {
            *sink.lock().unwrap() = r["meta"]["input_messages"].clone();
            Ok(Verdict::Keep)
        }));
        pipeline.process(
            call(Level::Info, "a", json!({"input_messages": "secret prompt"})),
            &Fixed,
        );
        assert_ne!(*seen.lock().unwrap(), json!("secret prompt"));
    }

    #[test]
    fn a_failing_redactor_drops_the_record_but_other_plugins_are_skipped() {
        let mut pipeline =
            Pipeline::new(Level::Info).with(FnPlugin::new("broken-redactor", true, |_| {
                Err("boom".to_string())
            }));
        let outcome = pipeline.process(call(Level::Info, "a", json!({"password": "p"})), &Fixed);
        assert_eq!(
            outcome.record,
            Err(Dropped::PluginFailed {
                plugin: "broken-redactor".into(),
                error: "boom".into()
            })
        );

        let mut lenient = Pipeline::new(Level::Info)
            .with(FnPlugin::new("flaky", false, |_| Err("boom".to_string())));
        let outcome = lenient.process(call(Level::Info, "a", json!({})), &Fixed);
        assert!(outcome.record.is_ok());
        assert_eq!(outcome.warnings.len(), 1);
    }

    #[test]
    fn a_panicking_redactor_also_withholds_the_record() {
        let mut pipeline =
            Pipeline::new(Level::Info).with(FnPlugin::new("panics", true, |_| panic!("bug")));
        let outcome = pipeline.process(call(Level::Info, "a", json!({})), &Fixed);
        assert!(matches!(outcome.record, Err(Dropped::PluginFailed { .. })));
    }

    #[test]
    fn redactors_run_in_order_and_retries_are_stamped() {
        let mut pipeline = Pipeline::new(Level::Info).with(KeyRedactor::secrets());
        let action = || json!({"kind": "action", "tool": "search", "password": "p"});
        let first = pipeline
            .process(call(Level::Info, "a", action()), &Fixed)
            .record
            .unwrap();
        let second = pipeline
            .process(call(Level::Info, "a", action()), &Fixed)
            .record
            .unwrap();
        assert_eq!(first["meta"]["password"], "***");
        assert!(first["meta"].get("retry_count").is_none());
        assert_eq!(second["meta"]["retry_count"], 1);
    }
}
