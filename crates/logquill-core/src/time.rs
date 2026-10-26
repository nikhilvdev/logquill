//! The record timestamp: UTC, ISO 8601, exactly three fraction digits
//! (`docs/spec/01-records-and-logger.md`). The clock is injected so tests are deterministic.

use std::time::{SystemTime, UNIX_EPOCH};

/// A source of "now". The core never reads the system clock directly.
pub trait Clock {
    /// Microseconds since the Unix epoch (negative before 1970).
    fn now_unix_micros(&self) -> i64;
}

/// The real clock.
#[derive(Clone, Copy, Debug, Default)]
pub struct SystemClock;

impl Clock for SystemClock {
    fn now_unix_micros(&self) -> i64 {
        match SystemTime::now().duration_since(UNIX_EPOCH) {
            Ok(after) => i64::try_from(after.as_micros()).unwrap_or(i64::MAX),
            Err(before) => -i64::try_from(before.duration().as_micros()).unwrap_or(i64::MAX),
        }
    }
}

/// Formats microseconds since the epoch as `YYYY-MM-DDTHH:MM:SS.mmmZ`.
///
/// The microseconds are truncated to milliseconds, which is what the Python release does after
/// rounding to whole microseconds.
pub fn format_timestamp(unix_micros: i64) -> String {
    let seconds = unix_micros.div_euclid(1_000_000);
    let millis = unix_micros.rem_euclid(1_000_000) / 1000;
    let days = seconds.div_euclid(86_400);
    let second_of_day = seconds.rem_euclid(86_400);
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{millis:03}Z",
        second_of_day / 3600,
        second_of_day % 3600 / 60,
        second_of_day % 60,
    )
}

/// Days since 1970-01-01 to a proleptic Gregorian (year, month, day), after Howard Hinnant's
/// `civil_from_days`.
fn civil_from_days(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let day_of_era = z.rem_euclid(146_097);
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let shifted_month = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * shifted_month + 2) / 5 + 1;
    let month = if shifted_month < 10 {
        shifted_month + 3
    } else {
        shifted_month - 9
    };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    (year, month, day)
}

#[cfg(test)]
mod tests {
    use super::*;

    // Expected values were produced by Python's `datetime` for the same instants.
    #[test]
    fn formats_known_instants() {
        let cases = [
            (0, "1970-01-01T00:00:00.000Z"),
            (1, "1970-01-01T00:00:00.000Z"),
            (999, "1970-01-01T00:00:00.000Z"),
            (1000, "1970-01-01T00:00:00.001Z"),
            (999_999, "1970-01-01T00:00:00.999Z"),
            (1_000_000, "1970-01-01T00:00:01.000Z"),
            (1_700_000_000_123_456, "2023-11-14T22:13:20.123Z"),
            (1_709_164_800_000_000, "2024-02-29T00:00:00.000Z"),
            (951_782_400_999_999, "2000-02-29T00:00:00.999Z"),
            (4_102_444_800_000_001, "2100-01-01T00:00:00.000Z"),
            (-1, "1969-12-31T23:59:59.999Z"),
            (-1_000_000, "1969-12-31T23:59:59.000Z"),
            (-86_400_000_000, "1969-12-31T00:00:00.000Z"),
            (-2_208_988_800_000_000, "1900-01-01T00:00:00.000Z"),
            (253_402_300_799_999_999, "9999-12-31T23:59:59.999Z"),
        ];
        for (micros, expected) in cases {
            assert_eq!(format_timestamp(micros), expected, "for {micros} µs");
        }
    }

    #[test]
    fn the_system_clock_is_after_2024() {
        assert!(SystemClock.now_unix_micros() > 1_704_067_200_000_000);
    }
}
