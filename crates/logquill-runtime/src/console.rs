//! The console transport: INFO and below to stdout, ERROR and FATAL to stderr, optionally in
//! colour (`docs/spec/05-runtime-and-transports.md`; decision D9).

use std::io::{IsTerminal, Write};

use serde_json::Value;

use crate::transport::{Formatter, Transport};

fn colour_code(level: &str) -> Option<&'static str> {
    Some(match level {
        "TRACE" => "\x1b[90m",
        "DEBUG" => "\x1b[36m",
        "INFO" => "\x1b[32m",
        "WARN" => "\x1b[33m",
        "ERROR" => "\x1b[31m",
        "FATAL" => "\x1b[35m",
        _ => return None,
    })
}

/// Whether colour should be on by default: only for a terminal, and never when `NO_COLOR` is set
/// (to anything non-empty).
pub fn colour_default() -> bool {
    std::env::var_os("NO_COLOR").map_or(true, |v| v.is_empty()) && std::io::stdout().is_terminal()
}

/// Writes each record's line to stdout or stderr, one write and flush per record.
pub struct Console {
    out: Box<dyn Write + Send>,
    err: Box<dyn Write + Send>,
    colour: bool,
    formatter: Formatter,
}

impl Console {
    /// The process's real stdout and stderr.
    pub fn stdio(colour: bool, formatter: Formatter) -> Console {
        Console::with_streams(
            Box::new(std::io::stdout()),
            Box::new(std::io::stderr()),
            colour,
            formatter,
        )
    }

    /// Any pair of streams, for tests and embedding.
    pub fn with_streams(
        out: Box<dyn Write + Send>,
        err: Box<dyn Write + Send>,
        colour: bool,
        formatter: Formatter,
    ) -> Console {
        Console {
            out,
            err,
            colour,
            formatter,
        }
    }
}

impl Transport for Console {
    fn format(&self, record: &Value) -> String {
        self.formatter.format(record)
    }

    fn write(&mut self, formatted: &str, record: &Value) -> Result<(), String> {
        let level = record.get("level").and_then(Value::as_str).unwrap_or("");
        let line = match (self.colour, colour_code(level)) {
            (true, Some(code)) => format!("{code}{formatted}\x1b[0m\n"),
            _ => format!("{formatted}\n"),
        };
        let stream = if matches!(level, "ERROR" | "FATAL") {
            &mut self.err
        } else {
            &mut self.out
        };
        stream
            .write_all(line.as_bytes())
            .and_then(|()| stream.flush())
            .map_err(|e| e.to_string())
    }

    fn flush(&mut self) -> Result<(), String> {
        self.out
            .flush()
            .and_then(|()| self.err.flush())
            .map_err(|e| e.to_string())
    }
}
