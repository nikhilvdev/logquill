//! Counting how often the same tool call is reopened (`docs/spec/01-records-and-logger.md`, "Retry tracking").

use std::collections::{BTreeMap, HashMap};

type Key = (String, String, String);

/// Remembers at most `max_entries` in-flight tool calls, the least recently used forgotten first.
#[derive(Clone, Debug)]
pub struct RetryTracker {
    max_entries: usize,
    clock: u64,
    attempts: HashMap<Key, (u32, u64)>,
    order: BTreeMap<u64, Key>,
}

impl Default for RetryTracker {
    fn default() -> Self {
        RetryTracker::new(1024)
    }
}

impl RetryTracker {
    /// A tracker bounded to `max_entries` calls (at least one).
    pub fn new(max_entries: usize) -> RetryTracker {
        RetryTracker {
            max_entries: max_entries.max(1),
            clock: 0,
            attempts: HashMap::new(),
            order: BTreeMap::new(),
        }
    }

    fn key(span_id: Option<&str>, tool: &str, call_id: Option<&str>) -> Key {
        (
            span_id.unwrap_or("").to_string(),
            tool.to_string(),
            call_id.unwrap_or("").to_string(),
        )
    }

    /// Another action for this call; returns its retry count (0 for the first attempt).
    pub fn opened(&mut self, span_id: Option<&str>, tool: &str, call_id: Option<&str>) -> u32 {
        let key = Self::key(span_id, tool, call_id);
        let count = match self.attempts.remove(&key) {
            Some((count, stamp)) => {
                self.order.remove(&stamp);
                count + 1
            }
            None => 0,
        };
        self.clock += 1;
        self.order.insert(self.clock, key.clone());
        self.attempts.insert(key, (count, self.clock));
        while self.attempts.len() > self.max_entries {
            if let Some((_, oldest)) = self.order.pop_first() {
                self.attempts.remove(&oldest);
            }
        }
        count
    }

    /// The call finished successfully: forget it.
    pub fn succeeded(&mut self, span_id: Option<&str>, tool: &str, call_id: Option<&str>) {
        if let Some((_, stamp)) = self.attempts.remove(&Self::key(span_id, tool, call_id)) {
            self.order.remove(&stamp);
        }
    }

    /// How many calls are remembered.
    pub fn len(&self) -> usize {
        self.attempts.len()
    }

    /// Whether nothing is remembered.
    pub fn is_empty(&self) -> bool {
        self.attempts.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counts_up_until_the_call_succeeds() {
        let mut t = RetryTracker::default();
        assert_eq!(t.opened(Some("s"), "search", None), 0);
        assert_eq!(t.opened(Some("s"), "search", None), 1);
        assert_eq!(t.opened(Some("s"), "search", Some("c1")), 0);
        t.succeeded(Some("s"), "search", None);
        assert_eq!(t.opened(Some("s"), "search", None), 0);
    }

    #[test]
    fn missing_and_empty_ids_are_the_same_key() {
        let mut t = RetryTracker::default();
        t.opened(None, "t", None);
        assert_eq!(t.opened(Some(""), "t", Some("")), 1);
    }

    #[test]
    fn the_oldest_call_is_forgotten_first() {
        let mut t = RetryTracker::new(2);
        t.opened(None, "a", None);
        t.opened(None, "b", None);
        t.opened(None, "a", None); // refreshes a
        t.opened(None, "c", None); // evicts b
        assert_eq!(t.len(), 2);
        assert_eq!(t.opened(None, "b", None), 0); // b was forgotten; this evicts a
        assert_eq!(t.opened(None, "a", None), 0);
    }
}
