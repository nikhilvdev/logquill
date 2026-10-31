//! Canonical JSON: the text hashed by the tamper chain and by the content policy. It reproduces
//! Python's `json.dumps(value, sort_keys=True)` byte for byte (`docs/spec/03-privacy-tracing-otel.md`).

use std::collections::BTreeMap;
use std::fmt;

use serde_json::Value;

/// A JSON value as the canonical text sees it. Unlike `serde_json::Value` it can hold the
/// non-finite numbers Python writes, and it keeps integers as their digits.
#[derive(Clone, Debug, PartialEq)]
pub enum Canon {
    Null,
    Bool(bool),
    /// An integer of any size, as its decimal digits.
    Int(String),
    Float(f64),
    Str(String),
    Arr(Vec<Canon>),
    /// Key/value pairs in source order; a repeated key keeps its last value when written.
    Obj(Vec<(String, Canon)>),
}

/// Why a piece of text is not JSON the canonicaliser can read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CanonError(pub String);

impl fmt::Display for CanonError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for CanonError {}

const MAX_DEPTH: usize = 256;

impl Canon {
    /// Reads JSON text, plus the words `NaN`, `Infinity` and `-Infinity` that Python's reader
    /// accepts. A lone surrogate escape (`\ud800`) or nesting deeper than 256 is an error.
    pub fn parse(text: &str) -> Result<Canon, CanonError> {
        let mut reader = Reader {
            bytes: text.as_bytes(),
            pos: 0,
        };
        let value = reader.value(0)?;
        reader.skip_space();
        if reader.pos != reader.bytes.len() {
            return Err(reader.error("unexpected text after the value"));
        }
        Ok(value)
    }

    /// The same value from a decoded `serde_json` value, keeping each number's spelling.
    pub fn from_value(value: &Value) -> Canon {
        match value {
            Value::Null => Canon::Null,
            Value::Bool(b) => Canon::Bool(*b),
            Value::Number(n) => number(&n.to_string()),
            Value::String(s) => Canon::Str(s.clone()),
            Value::Array(items) => Canon::Arr(items.iter().map(Canon::from_value).collect()),
            Value::Object(map) => Canon::Obj(
                map.iter()
                    .map(|(k, v)| (k.clone(), Canon::from_value(v)))
                    .collect(),
            ),
        }
    }

    /// The canonical text: `", "` and `": "` separators, keys by code point, ASCII only.
    pub fn to_text(&self) -> String {
        let mut out = String::new();
        self.write(&mut out);
        out
    }

    fn write(&self, out: &mut String) {
        match self {
            Canon::Null => out.push_str("null"),
            Canon::Bool(true) => out.push_str("true"),
            Canon::Bool(false) => out.push_str("false"),
            Canon::Int(digits) => out.push_str(digits),
            Canon::Float(x) => out.push_str(&python_float_repr(*x)),
            Canon::Str(s) => write_string(s, out),
            Canon::Arr(items) => {
                out.push('[');
                for (i, item) in items.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    item.write(out);
                }
                out.push(']');
            }
            Canon::Obj(pairs) => {
                let mut sorted: BTreeMap<&str, &Canon> = BTreeMap::new();
                for (k, v) in pairs {
                    sorted.insert(k, v);
                }
                out.push('{');
                for (i, (k, v)) in sorted.iter().enumerate() {
                    if i > 0 {
                        out.push_str(", ");
                    }
                    write_string(k, out);
                    out.push_str(": ");
                    v.write(out);
                }
                out.push('}');
            }
        }
    }
}

/// The canonical text of a decoded JSON value.
pub fn canonical_json(value: &Value) -> String {
    Canon::from_value(value).to_text()
}

/// A number from its source spelling: an integer keeps its digits (`-0` is `0`, as in Python),
/// anything with a fraction or exponent is a float.
fn number(lexeme: &str) -> Canon {
    if lexeme.bytes().any(|b| matches!(b, b'.' | b'e' | b'E')) {
        Canon::Float(lexeme.parse::<f64>().unwrap_or(f64::NAN))
    } else if lexeme == "-0" {
        Canon::Int("0".to_string())
    } else {
        Canon::Int(lexeme.to_string())
    }
}

/// Python's `repr(float)`: the shortest digits that round-trip, fixed notation from `1e-4` up to
/// (not including) `1e16`, otherwise `d.ddde±XX` with at least two exponent digits.
pub fn python_float_repr(x: f64) -> String {
    if x.is_nan() {
        return "NaN".to_string();
    }
    if x.is_infinite() {
        return if x < 0.0 { "-Infinity" } else { "Infinity" }.to_string();
    }
    let sign = if x.is_sign_negative() { "-" } else { "" };
    if x == 0.0 {
        return format!("{sign}0.0");
    }
    let sci = format!("{:e}", x.abs());
    let (mantissa, exp) = sci.split_once('e').unwrap_or((&sci, "0"));
    let exp: i32 = exp.parse().unwrap_or(0);
    let digits: String = mantissa.chars().filter(|c| *c != '.').collect();
    let point = exp + 1;
    let body = if (-3..=16).contains(&point) {
        let len = digits.len() as i32;
        if point <= 0 {
            format!("0.{}{}", "0".repeat((-point) as usize), digits)
        } else if len <= point {
            format!("{}{}.0", digits, "0".repeat((point - len) as usize))
        } else {
            format!(
                "{}.{}",
                &digits[..point as usize],
                &digits[point as usize..]
            )
        }
    } else {
        let rest = &digits[1..];
        let fraction = if rest.is_empty() {
            String::new()
        } else {
            format!(".{rest}")
        };
        let exp_sign = if exp < 0 { '-' } else { '+' };
        format!("{}{}e{}{:02}", &digits[..1], fraction, exp_sign, exp.abs())
    };
    format!("{sign}{body}")
}

fn write_string(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
}

struct Reader<'a> {
    bytes: &'a [u8],
    pos: usize,
}

impl Reader<'_> {
    fn error(&self, what: &str) -> CanonError {
        CanonError(format!("invalid JSON at byte {}: {what}", self.pos))
    }

    fn skip_space(&mut self) {
        while matches!(self.bytes.get(self.pos), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.pos += 1;
        }
    }

    fn word(&mut self, word: &str, value: Canon) -> Result<Canon, CanonError> {
        if self.bytes[self.pos..].starts_with(word.as_bytes()) {
            self.pos += word.len();
            Ok(value)
        } else {
            Err(self.error("unexpected character"))
        }
    }

    fn value(&mut self, depth: usize) -> Result<Canon, CanonError> {
        if depth > MAX_DEPTH {
            return Err(self.error("nesting is too deep"));
        }
        self.skip_space();
        match self.bytes.get(self.pos) {
            None => Err(self.error("unexpected end of text")),
            Some(b'n') => self.word("null", Canon::Null),
            Some(b't') => self.word("true", Canon::Bool(true)),
            Some(b'f') => self.word("false", Canon::Bool(false)),
            Some(b'N') => self.word("NaN", Canon::Float(f64::NAN)),
            Some(b'I') => self.word("Infinity", Canon::Float(f64::INFINITY)),
            Some(b'"') => Ok(Canon::Str(self.string()?)),
            Some(b'[') => self.array(depth),
            Some(b'{') => self.object(depth),
            Some(b'-' | b'0'..=b'9') => self.number(),
            Some(_) => Err(self.error("unexpected character")),
        }
    }

    fn number(&mut self) -> Result<Canon, CanonError> {
        if self.bytes[self.pos..].starts_with(b"-Infinity") {
            self.pos += 9;
            return Ok(Canon::Float(f64::NEG_INFINITY));
        }
        let start = self.pos;
        if self.bytes[self.pos] == b'-' {
            self.pos += 1;
        }
        let digits = |r: &mut Self| {
            let from = r.pos;
            while matches!(r.bytes.get(r.pos), Some(b'0'..=b'9')) {
                r.pos += 1;
            }
            r.pos > from
        };
        let int_start = self.pos;
        if !digits(self) {
            return Err(self.error("a number needs digits"));
        }
        if self.bytes[int_start] == b'0' && self.pos - int_start > 1 {
            return Err(self.error("a number cannot have leading zeros"));
        }
        if self.bytes.get(self.pos) == Some(&b'.') {
            self.pos += 1;
            if !digits(self) {
                return Err(self.error("a fraction needs digits"));
            }
        }
        if matches!(self.bytes.get(self.pos), Some(b'e' | b'E')) {
            self.pos += 1;
            if matches!(self.bytes.get(self.pos), Some(b'+' | b'-')) {
                self.pos += 1;
            }
            if !digits(self) {
                return Err(self.error("an exponent needs digits"));
            }
        }
        let lexeme = std::str::from_utf8(&self.bytes[start..self.pos]).unwrap_or("0");
        Ok(number(lexeme))
    }

    fn hex4(&mut self) -> Result<u32, CanonError> {
        let slice = self
            .bytes
            .get(self.pos..self.pos + 4)
            .ok_or_else(|| self.error("short \\u escape"))?;
        let text = std::str::from_utf8(slice).map_err(|_| self.error("bad \\u escape"))?;
        let unit = u32::from_str_radix(text, 16).map_err(|_| self.error("bad \\u escape"))?;
        self.pos += 4;
        Ok(unit)
    }

    fn string(&mut self) -> Result<String, CanonError> {
        self.pos += 1;
        let mut out = String::new();
        loop {
            let Some(&byte) = self.bytes.get(self.pos) else {
                return Err(self.error("unterminated string"));
            };
            match byte {
                b'"' => {
                    self.pos += 1;
                    return Ok(out);
                }
                b'\\' => {
                    self.pos += 1;
                    let Some(&esc) = self.bytes.get(self.pos) else {
                        return Err(self.error("unterminated escape"));
                    };
                    self.pos += 1;
                    match esc {
                        b'"' => out.push('"'),
                        b'\\' => out.push('\\'),
                        b'/' => out.push('/'),
                        b'b' => out.push('\u{8}'),
                        b'f' => out.push('\u{c}'),
                        b'n' => out.push('\n'),
                        b'r' => out.push('\r'),
                        b't' => out.push('\t'),
                        b'u' => {
                            let first = self.hex4()?;
                            let point = if (0xD800..0xDC00).contains(&first) {
                                if self.bytes[self.pos..].starts_with(b"\\u") {
                                    self.pos += 2;
                                    let second = self.hex4()?;
                                    if !(0xDC00..0xE000).contains(&second) {
                                        return Err(self.error("a lone surrogate escape"));
                                    }
                                    0x10000 + ((first - 0xD800) << 10) + (second - 0xDC00)
                                } else {
                                    return Err(self.error("a lone surrogate escape"));
                                }
                            } else if (0xDC00..0xE000).contains(&first) {
                                return Err(self.error("a lone surrogate escape"));
                            } else {
                                first
                            };
                            out.push(
                                char::from_u32(point)
                                    .ok_or_else(|| self.error("bad code point"))?,
                            );
                        }
                        _ => return Err(self.error("unknown escape")),
                    }
                }
                0..=0x1f => return Err(self.error("a raw control character in a string")),
                _ => {
                    let rest = std::str::from_utf8(&self.bytes[self.pos..])
                        .map_err(|_| self.error("bad UTF-8"))?;
                    let c = rest
                        .chars()
                        .next()
                        .ok_or_else(|| self.error("unterminated string"))?;
                    out.push(c);
                    self.pos += c.len_utf8();
                }
            }
        }
    }

    fn array(&mut self, depth: usize) -> Result<Canon, CanonError> {
        self.pos += 1;
        let mut items = Vec::new();
        self.skip_space();
        if self.bytes.get(self.pos) == Some(&b']') {
            self.pos += 1;
            return Ok(Canon::Arr(items));
        }
        loop {
            items.push(self.value(depth + 1)?);
            self.skip_space();
            match self.bytes.get(self.pos) {
                Some(b',') => self.pos += 1,
                Some(b']') => {
                    self.pos += 1;
                    return Ok(Canon::Arr(items));
                }
                _ => return Err(self.error("expected , or ]")),
            }
        }
    }

    fn object(&mut self, depth: usize) -> Result<Canon, CanonError> {
        self.pos += 1;
        let mut pairs = Vec::new();
        self.skip_space();
        if self.bytes.get(self.pos) == Some(&b'}') {
            self.pos += 1;
            return Ok(Canon::Obj(pairs));
        }
        loop {
            self.skip_space();
            if self.bytes.get(self.pos) != Some(&b'"') {
                return Err(self.error("expected a string key"));
            }
            let key = self.string()?;
            self.skip_space();
            if self.bytes.get(self.pos) != Some(&b':') {
                return Err(self.error("expected :"));
            }
            self.pos += 1;
            pairs.push((key, self.value(depth + 1)?));
            self.skip_space();
            match self.bytes.get(self.pos) {
                Some(b',') => self.pos += 1,
                Some(b'}') => {
                    self.pos += 1;
                    return Ok(Canon::Obj(pairs));
                }
                _ => return Err(self.error("expected , or }")),
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_follow_pythons_repr() {
        let cases = [
            (0.1, "0.1"),
            (100.0, "100.0"),
            (-0.0, "-0.0"),
            (0.0, "0.0"),
            (1e16, "1e+16"),
            (1e15, "1000000000000000.0"),
            (1e-5, "1e-05"),
            (0.0001, "0.0001"),
            (1.5e300, "1.5e+300"),
            (5e-324, "5e-324"),
            (1.7976931348623157e308, "1.7976931348623157e+308"),
            (123456.789, "123456.789"),
            (2e-5, "2e-05"),
            (1.5e-7, "1.5e-07"),
            (f64::INFINITY, "Infinity"),
            (f64::NEG_INFINITY, "-Infinity"),
        ];
        for (x, text) in cases {
            assert_eq!(python_float_repr(x), text, "for {x:e}");
        }
        assert_eq!(python_float_repr(f64::NAN), "NaN");
    }

    #[test]
    fn keys_sort_by_code_point_and_text_is_ascii() {
        let canon = Canon::parse(r#"{"\ud834\udd1e":1,"\uffff":2,"a":3,"é":4}"#).unwrap();
        assert_eq!(
            canon.to_text(),
            r#"{"a": 3, "\u00e9": 4, "\uffff": 2, "\ud834\udd1e": 1}"#
        );
    }

    #[test]
    fn integers_keep_every_digit_and_negative_zero_is_zero() {
        let canon = Canon::parse("[-0, 18446744073709551617, -0.0, 12.0, 1E+2]").unwrap();
        assert_eq!(
            canon.to_text(),
            "[0, 18446744073709551617, -0.0, 12.0, 100.0]"
        );
    }

    #[test]
    fn hostile_text_is_an_error_not_a_panic() {
        for text in [
            "",
            "[",
            "{\"a\"",
            "\"\\ud800\"",
            "01",
            "1.",
            "tru",
            "[1,]",
            "\"a\u{1}\"",
        ] {
            assert!(Canon::parse(text).is_err(), "{text:?}");
        }
        assert!(Canon::parse(&"[".repeat(300)).is_err());
    }
}
