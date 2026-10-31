//! Colour, with the same level colours as the console transport.

use std::io::IsTerminal;

use serde_json::Value;

fn code(level: &str) -> Option<&'static str> {
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

/// Colour only for a terminal, unless `--no-color` or a non-empty `NO_COLOR` says otherwise.
pub fn colour_wanted(no_color_flag: bool) -> bool {
    !no_color_flag
        && std::env::var_os("NO_COLOR").map_or(true, |v| v.is_empty())
        && std::io::stdout().is_terminal()
}

/// `text` wrapped in the colour of `level` when `on`; unchanged for an unknown level.
pub fn paint(level: &Value, text: String, on: bool) -> String {
    match (on, level.as_str().and_then(code)) {
        (true, Some(code)) => format!("{code}{text}\x1b[0m"),
        _ => text,
    }
}
