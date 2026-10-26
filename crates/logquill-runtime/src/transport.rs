//! The transport contract (`docs/spec/05-runtime-and-transports.md`): format a record, write it,
//! optionally flush, and close. A transport never has to survive a bad record; the dispatcher
//! catches whatever it returns or throws.

use logquill_core::{format_json, format_logfmt, format_text};
use serde_json::Value;

/// How a record becomes a line.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Formatter {
    /// One compact JSON object (the default).
    #[default]
    Json,
    /// A human-readable entry.
    Text,
    /// `key=value` pairs.
    Logfmt,
}

impl Formatter {
    /// The formatter named `json`, `text` or `logfmt`.
    pub fn parse(name: &str) -> Result<Formatter, String> {
        match name {
            "json" => Ok(Formatter::Json),
            "text" => Ok(Formatter::Text),
            "logfmt" => Ok(Formatter::Logfmt),
            other => Err(format!(
                "Unknown formatter '{other}' \u{2014} built-in formatters are: json, logfmt, text."
            )),
        }
    }

    /// The line for `record`.
    pub fn format(self, record: &Value) -> String {
        match self {
            Formatter::Json => format_json(record),
            Formatter::Text => format_text(record),
            Formatter::Logfmt => format_logfmt(record),
        }
    }
}

/// A sink for records.
pub trait Transport: Send {
    /// The line this transport writes for `record`.
    fn format(&self, record: &Value) -> String;

    /// Writes one record. An `Err` is reported by the dispatcher and never stops logging.
    fn write(&mut self, formatted: &str, record: &Value) -> Result<(), String>;

    /// Sends whatever is buffered now. Releases nothing.
    fn flush(&mut self) -> Result<(), String> {
        Ok(())
    }

    /// Flushes and releases resources. Called once.
    fn close(&mut self) -> Result<(), String> {
        self.flush()
    }
}
