//! Per-logger enable/disable rules (`docs/spec/01-records-and-logger.md`, "Toggle").

use std::collections::HashMap;

/// A set of name → enabled rules. The most specific rule wins: `"mylib"` covers `"mylib"` and
/// `"mylib.http"` but not `"mylib2"`; `""` is the root.
#[derive(Clone, Debug, Default)]
pub struct Toggle {
    rules: HashMap<String, bool>,
}

impl Toggle {
    /// A toggle with no rules: everything is enabled.
    pub fn new() -> Toggle {
        Toggle::default()
    }

    /// Silences `name` and everything under it.
    pub fn disable(&mut self, name: &str) {
        self.rules.insert(name.to_string(), false);
    }

    /// Turns `name` and everything under it back on.
    pub fn enable(&mut self, name: &str) {
        self.rules.insert(name.to_string(), true);
    }

    /// Forgets every rule.
    pub fn reset(&mut self) {
        self.rules.clear();
    }

    /// Whether `name` may log: the rule for `name`, else for its nearest parent, else the root rule, else yes.
    pub fn is_enabled(&self, name: &str) -> bool {
        if self.rules.is_empty() {
            return true;
        }
        let mut current = name;
        loop {
            if let Some(&enabled) = self.rules.get(current) {
                return enabled;
            }
            match current.rfind('.') {
                Some(cut) => current = &current[..cut],
                None => break,
            }
        }
        self.rules.get("").copied().unwrap_or(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_most_specific_rule_wins() {
        let mut t = Toggle::new();
        assert!(t.is_enabled("anything"));
        t.disable("mylib");
        t.enable("mylib.http");
        assert!(!t.is_enabled("mylib"));
        assert!(!t.is_enabled("mylib.db"));
        assert!(t.is_enabled("mylib.http"));
        assert!(t.is_enabled("mylib.http.client"));
        assert!(t.is_enabled("mylib2"));
    }

    #[test]
    fn the_root_rule_covers_everything_else() {
        let mut t = Toggle::new();
        t.disable("");
        t.enable("app");
        assert!(!t.is_enabled("other.x"));
        assert!(t.is_enabled("app.api"));
        t.reset();
        assert!(t.is_enabled("other"));
    }
}
