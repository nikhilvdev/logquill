//! Streaming JSONL: one line at a time, never the whole file.

use std::io::{self, BufRead, BufReader, Read};
use std::ops::ControlFlow;

use serde_json::Value;

/// Calls `f` with each non-blank line, without its line ending, until it breaks or the input ends.
/// Bytes that are not UTF-8 are replaced rather than ending the read, so one damaged line cannot
/// hide everything after it.
pub fn for_each_line(
    reader: impl Read,
    mut f: impl FnMut(&str) -> ControlFlow<()>,
) -> io::Result<()> {
    let mut reader = BufReader::new(reader);
    let mut buffer = Vec::new();
    loop {
        buffer.clear();
        if reader.read_until(b'\n', &mut buffer)? == 0 {
            return Ok(());
        }
        let line = String::from_utf8_lossy(&buffer);
        let line = line.trim_end_matches(['\n', '\r']);
        if !line.trim().is_empty() && f(line).is_break() {
            return Ok(());
        }
    }
}

/// `text` quoted the way Python's `repr` does (single quotes unless the text has a `'` and no `"`),
/// so warnings read the same as the old CLI's.
pub fn py_repr(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for c in text.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 || c as u32 == 0x7f => {
                out.push_str(&format!("\\x{:02x}", c as u32))
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

/// The record on a line. With `command`, a line that is not a JSON object is reported on stderr as
/// `logquill <command>: skipping …`; without it, silently.
pub fn parse_line(line: &str, command: Option<&str>) -> Option<Value> {
    let skip = |what: &str| {
        if let Some(command) = command {
            let shown: String = line.chars().take(200).collect();
            eprintln!("logquill {command}: skipping {what}: {}", py_repr(&shown));
        }
    };
    match serde_json::from_str::<Value>(line) {
        Ok(value) if value.is_object() => Some(value),
        Ok(_) => {
            skip("non-object JSON line");
            None
        }
        Err(_) => {
            skip("malformed JSON line");
            None
        }
    }
}

/// The JSON objects in a source, as an iterator; lines that are not objects are skipped silently.
pub fn records(reader: impl Read) -> impl Iterator<Item = Value> {
    BufReader::new(reader)
        .split(b'\n')
        .map_while(Result::ok)
        .filter_map(|bytes| parse_line(&String::from_utf8_lossy(&bytes), None))
}
