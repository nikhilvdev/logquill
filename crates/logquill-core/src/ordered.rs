//! A map that remembers the order entries were last touched, for the bounded tables the plugins
//! keep (trace buffers, rate-limit windows, flushed runs). Oldest-first eviction is a `pop_oldest`.

use std::collections::{BTreeMap, HashMap};

use serde_json::Value;

/// A hashable stand-in for a JSON value that tells `1` from `1.0` and `"1"` from `1`.
pub(crate) fn value_key(value: &Value) -> String {
    format!(
        "{}:{}",
        crate::jsonx::json_type(value),
        crate::jsonx::json_line(value)
    )
}

#[derive(Clone, Debug)]
pub(crate) struct Ordered<V> {
    clock: u64,
    items: HashMap<String, (V, u64)>,
    order: BTreeMap<u64, String>,
}

impl<V> Default for Ordered<V> {
    fn default() -> Self {
        Ordered {
            clock: 0,
            items: HashMap::new(),
            order: BTreeMap::new(),
        }
    }
}

impl<V> Ordered<V> {
    pub(crate) fn len(&self) -> usize {
        self.items.len()
    }

    pub(crate) fn contains(&self, key: &str) -> bool {
        self.items.contains_key(key)
    }

    pub(crate) fn get(&self, key: &str) -> Option<&V> {
        self.items.get(key).map(|(value, _)| value)
    }

    pub(crate) fn get_mut(&mut self, key: &str) -> Option<&mut V> {
        self.items.get_mut(key).map(|(value, _)| value)
    }

    /// Marks `key` as the most recently used.
    pub(crate) fn touch(&mut self, key: &str) {
        if let Some((_, stamp)) = self.items.get_mut(key) {
            self.order.remove(stamp);
            self.clock += 1;
            *stamp = self.clock;
            self.order.insert(self.clock, key.to_string());
        }
    }

    /// Adds or replaces `key`, making it the most recently used.
    pub(crate) fn insert(&mut self, key: String, value: V) {
        if let Some((_, stamp)) = self.items.remove(&key) {
            self.order.remove(&stamp);
        }
        self.clock += 1;
        self.order.insert(self.clock, key.clone());
        self.items.insert(key, (value, self.clock));
    }

    pub(crate) fn remove(&mut self, key: &str) -> Option<V> {
        let (value, stamp) = self.items.remove(key)?;
        self.order.remove(&stamp);
        Some(value)
    }

    pub(crate) fn pop_oldest(&mut self) -> Option<V> {
        let (_, key) = self.order.pop_first()?;
        self.items.remove(&key).map(|(value, _)| value)
    }

    /// Values from oldest to newest.
    pub(crate) fn values(&self) -> impl Iterator<Item = &V> {
        self.order
            .values()
            .filter_map(|key| self.items.get(key).map(|(value, _)| value))
    }

    /// Keys from oldest to newest.
    pub(crate) fn keys(&self) -> impl Iterator<Item = &String> {
        self.order.values()
    }

    pub(crate) fn clear(&mut self) {
        self.items.clear();
        self.order.clear();
    }
}
