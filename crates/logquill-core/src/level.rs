//! Log levels: names, weights and parsing (`docs/spec/01-records-and-logger.md`).

use std::fmt;

use serde_json::Value;

/// The six levels, ordered by severity. A record carries the upper-case name; level filters
/// compare the weight.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum Level {
    Trace = 5,
    Debug = 10,
    Info = 20,
    Warn = 30,
    Error = 40,
    Fatal = 50,
}

/// Why an input is not a level. `Display` gives the message every language uses.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LevelError {
    /// A name or number that is not one of the six levels; holds the text to show.
    Unknown(String),
    /// A value that is neither a name nor a number (`null`, a boolean, a list, an object).
    WrongType(&'static str),
}

impl fmt::Display for LevelError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            LevelError::Unknown(shown) => write!(f, "Unknown log level: {shown}"),
            LevelError::WrongType(got) => {
                write!(f, "level must be a name or a number, got {got}")
            }
        }
    }
}

impl std::error::Error for LevelError {}

impl Level {
    /// Every level, from least to most severe.
    pub const ALL: [Level; 6] = [
        Level::Trace,
        Level::Debug,
        Level::Info,
        Level::Warn,
        Level::Error,
        Level::Fatal,
    ];

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

    /// The numeric weight (5, 10, 20, 30, 40, 50).
    pub const fn weight(self) -> u8 {
        self as u8
    }

    /// The level whose upper-case name is exactly `name`, as a record spells it.
    pub fn from_record_name(name: &str) -> Option<Level> {
        Level::ALL.into_iter().find(|level| level.name() == name)
    }

    /// The level named `name`, in any ASCII case (`"info"`, `"Warn"`). Only ASCII letters are
    /// folded, so `"ınfo"` (dotless i) is not `INFO`.
    pub fn parse(name: &str) -> Result<Level, LevelError> {
        Level::from_record_name(&name.to_ascii_uppercase())
            .ok_or_else(|| LevelError::Unknown(name.to_string()))
    }

    /// The level with exactly this numeric weight.
    pub fn from_weight(weight: i64) -> Result<Level, LevelError> {
        Level::ALL
            .into_iter()
            .find(|level| i64::from(level.weight()) == weight)
            .ok_or_else(|| LevelError::Unknown(weight.to_string()))
    }

    /// A level given as a decoded JSON value: a name, or a whole-number weight. A float such as
    /// `20.0` is not a weight.
    pub fn from_json(value: &Value) -> Result<Level, LevelError> {
        match value {
            Value::String(name) => Level::parse(name),
            Value::Number(number) => match number.as_i64() {
                Some(weight) => Level::from_weight(weight),
                None => Err(LevelError::Unknown(number.to_string())),
            },
            Value::Null => Err(LevelError::WrongType("null")),
            Value::Bool(_) => Err(LevelError::WrongType("boolean")),
            Value::Array(_) => Err(LevelError::WrongType("array")),
            Value::Object(_) => Err(LevelError::WrongType("object")),
        }
    }
}

impl fmt::Display for Level {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.name())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn weights_and_names_match_the_contract() {
        let expected = [
            (Level::Trace, 5, "TRACE"),
            (Level::Debug, 10, "DEBUG"),
            (Level::Info, 20, "INFO"),
            (Level::Warn, 30, "WARN"),
            (Level::Error, 40, "ERROR"),
            (Level::Fatal, 50, "FATAL"),
        ];
        for (level, weight, name) in expected {
            assert_eq!(level.weight(), weight);
            assert_eq!(level.name(), name);
            assert_eq!(level.to_string(), name);
        }
        assert!(Level::Debug < Level::Info);
    }

    #[test]
    fn a_name_parses_in_any_case() {
        assert_eq!(Level::parse("info"), Ok(Level::Info));
        assert_eq!(Level::parse("Warn"), Ok(Level::Warn));
        assert_eq!(Level::parse("FATAL"), Ok(Level::Fatal));
    }

    #[test]
    fn a_record_name_must_be_upper_case() {
        assert_eq!(Level::from_record_name("ERROR"), Some(Level::Error));
        assert_eq!(Level::from_record_name("error"), None);
    }

    #[test]
    fn only_the_six_weights_are_levels() {
        assert_eq!(Level::from_weight(20), Ok(Level::Info));
        assert_eq!(
            Level::from_weight(25),
            Err(LevelError::Unknown("25".to_string()))
        );
    }

    #[test]
    fn an_unknown_level_says_so() {
        let error = Level::parse("verbose").unwrap_err();
        assert_eq!(error.to_string(), "Unknown log level: verbose");
    }
}
