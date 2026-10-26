//! LogQuill core: pure logic shared by every language binding.
//!
//! No I/O and no threads live here; the clock and random source are injected so every
//! behaviour is deterministic under test. The behaviour to reproduce is written down in
//! `docs/spec/`.

/// The record shape this version writes (`schema/record.schema.json`).
pub const SCHEMA_VERSION: &str = "2.0";

/// What a record with no `schema_version` (anything written by logquill 1.x) is read as.
pub const LEGACY_SCHEMA_VERSION: &str = "1.0";

/// Log levels. The name is what a record carries; the weight orders them.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum Level {
    Trace = 5,
    Debug = 10,
    Info = 20,
    Warn = 30,
    Error = 40,
    Fatal = 50,
}

impl Level {
    /// The upper-case name written into a record's `level` field.
    pub const fn name(self) -> &'static str {
        match self {
            Level::Trace => "TRACE",
            Level::Debug => "DEBUG",
            Level::Info => "INFO",
            Level::Warn => "WARN",
            Level::Error => "ERROR",
            Level::Fatal => "FATAL",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn weights_and_names_match_the_contract() {
        let all = [
            (Level::Trace, 5, "TRACE"),
            (Level::Debug, 10, "DEBUG"),
            (Level::Info, 20, "INFO"),
            (Level::Warn, 30, "WARN"),
            (Level::Error, 40, "ERROR"),
            (Level::Fatal, 50, "FATAL"),
        ];
        for (level, weight, name) in all {
            assert_eq!(level as i32, weight);
            assert_eq!(level.name(), name);
        }
        assert!(Level::Debug < Level::Info);
    }
}
