//! Rebuilding one run's span tree from its records and rendering it
//! (`docs/spec/06-cli-and-viewer.md`; a record that would become its own ancestor is a root).

use serde_json::{json, Value};

use crate::format::shown;
use crate::jsonx::is_integer;
use crate::ordered::Ordered;

/// Token and cost totals, one node's own or a subtree's.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Rollup {
    pub tokens_in: i128,
    pub tokens_out: i128,
    pub cost_usd: f64,
}

/// One record and everything nested directly inside it, in the order it was written.
#[derive(Clone, Debug)]
pub struct TreeNode {
    /// The record this node was built from.
    pub record: Value,
    pub level: Value,
    pub message: Value,
    pub rollup: Rollup,
    pub duration_ms: Option<f64>,
    pub children: Vec<TreeNode>,
}

/// The result of [`build_trace`].
#[derive(Clone, Debug)]
pub struct Trace {
    /// How many records belonged to the run.
    pub record_count: usize,
    pub roots: Vec<TreeNode>,
}

struct Raw {
    record: Value,
    level: Value,
    message: Value,
    own: Rollup,
    duration_ms: Option<f64>,
    children: Vec<usize>,
}

fn number(value: Option<&Value>) -> Option<f64> {
    match value {
        Some(Value::Number(n)) => n.to_string().parse().ok(),
        _ => None,
    }
}

fn contains(nodes: &[Raw], root: usize, target: usize) -> bool {
    let mut stack = vec![root];
    while let Some(at) = stack.pop() {
        if at == target {
            return true;
        }
        stack.extend(&nodes[at].children);
    }
    false
}

fn rollup(nodes: &[Raw], at: usize) -> Rollup {
    let mut total = nodes[at].own;
    for &child in &nodes[at].children {
        let part = rollup(nodes, child);
        total = Rollup {
            tokens_in: total.tokens_in + part.tokens_in,
            tokens_out: total.tokens_out + part.tokens_out,
            cost_usd: total.cost_usd + part.cost_usd,
        };
    }
    total
}

fn finish(nodes: &[Raw], at: usize) -> TreeNode {
    TreeNode {
        record: nodes[at].record.clone(),
        level: nodes[at].level.clone(),
        message: nodes[at].message.clone(),
        rollup: rollup(nodes, at),
        duration_ms: nodes[at].duration_ms,
        children: nodes[at]
            .children
            .iter()
            .map(|&c| finish(nodes, c))
            .collect(),
    }
}

/// Builds the tree of the records whose `meta.run_id` is `run_id`. Everything logged inside a
/// span is written before the span's own record, so one pass suffices; a node whose parent never
/// closed is kept as a root after the others.
pub fn build_trace<'a>(records: impl IntoIterator<Item = &'a Value>, run_id: &str) -> Trace {
    let mut nodes: Vec<Raw> = Vec::new();
    let mut by_span: Ordered<usize> = Ordered::default();
    let mut pending: Ordered<Vec<usize>> = Ordered::default();
    let mut roots: Vec<usize> = Vec::new();
    let mut count = 0;
    for record in records {
        let meta = record.get("meta").and_then(Value::as_object);
        if meta.and_then(|m| m.get("run_id")).and_then(Value::as_str) != Some(run_id) {
            continue;
        }
        count += 1;
        let text = |key: &str| match meta.and_then(|m| m.get(key)) {
            Some(Value::String(s)) if !s.is_empty() => Some(s.clone()),
            _ => None,
        };
        let (span, parent) = (text("span_id"), text("parent_span_id"));
        let llm = record.get("llm").and_then(Value::as_object);
        let int = |key: &str| {
            llm.and_then(|l| l.get(key))
                .filter(|v| is_integer(v))
                .and_then(|v| v.to_string().parse::<i128>().ok())
        };
        let me = nodes.len();
        nodes.push(Raw {
            record: record.clone(),
            level: record.get("level").cloned().unwrap_or(Value::Null),
            message: record.get("message").cloned().unwrap_or(Value::Null),
            own: Rollup {
                tokens_in: int("tokens_in").unwrap_or(0),
                tokens_out: int("tokens_out").unwrap_or(0),
                cost_usd: number(llm.and_then(|l| l.get("cost_usd"))).unwrap_or(0.0),
            },
            duration_ms: number(meta.and_then(|m| m.get("duration_ms"))),
            children: Vec::new(),
        });
        if let Some(span) = &span {
            nodes[me].children = pending.remove(span).unwrap_or_default();
            by_span.insert(span.clone(), me);
        }
        let parent_node = parent.as_ref().and_then(|p| by_span.get(p).copied());
        match (&parent, parent_node) {
            (None, _) => roots.push(me),
            (Some(_), Some(p)) if contains(&nodes, me, p) => roots.push(me),
            (Some(_), Some(p)) => nodes[p].children.push(me),
            (Some(key), None) => {
                if let Some(list) = pending.get_mut(key) {
                    list.push(me);
                } else {
                    pending.insert(key.clone(), vec![me]);
                }
            }
        }
    }
    let orphans: Vec<usize> = pending.values().flatten().copied().collect();
    roots.extend(orphans);
    Trace {
        record_count: count,
        roots: roots.iter().map(|&r| finish(&nodes, r)).collect(),
    }
}

fn group(digits: &str) -> String {
    let mut out = String::new();
    for (i, c) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(c);
    }
    out
}

/// `format(x, ",.{places}f")`.
fn thousands(x: f64, places: usize) -> String {
    let text = format!("{:.*}", places, x.abs());
    let (whole, fraction) = text.split_once('.').unwrap_or((&text, ""));
    let sign = if x.is_sign_negative() && x != 0.0 {
        "-"
    } else {
        ""
    };
    if places == 0 {
        format!("{sign}{}", group(whole))
    } else {
        format!("{sign}{}.{fraction}", group(whole))
    }
}

fn label(node: &TreeNode) -> String {
    let mut parts = Vec::new();
    if let Some(duration) = node.duration_ms {
        parts.push(format!("{}ms", thousands(duration, 1)));
    }
    if node.rollup.tokens_in != 0 || node.rollup.tokens_out != 0 {
        parts.push(format!(
            "{}\u{2192}{} tok",
            node.rollup.tokens_in, node.rollup.tokens_out
        ));
    }
    if node.rollup.cost_usd != 0.0 {
        parts.push(format!("${}", thousands(node.rollup.cost_usd, 4)));
    }
    let note = if parts.is_empty() {
        String::new()
    } else {
        format!("  ({})", parts.join(", "))
    };
    format!(
        "[{}] {}{}",
        shown(Some(&node.level), "?"),
        shown(Some(&node.message), ""),
        note
    )
}

/// Wraps a node's label (given its level) in whatever styling the caller wants.
pub type Paint<'a> = &'a dyn Fn(&Value, String) -> String;

fn walk(node: &TreeNode, prefix: &str, last: bool, paint: Paint, lines: &mut Vec<String>) {
    let connector = if last {
        "\u{2514}\u{2500} "
    } else {
        "\u{251c}\u{2500} "
    };
    lines.push(format!(
        "{prefix}{connector}{}",
        paint(&node.level, label(node))
    ));
    let child_prefix = format!("{prefix}{}", if last { "   " } else { "\u{2502}  " });
    for (i, child) in node.children.iter().enumerate() {
        walk(
            child,
            &child_prefix,
            i + 1 == node.children.len(),
            paint,
            lines,
        );
    }
}

/// The tree as indented lines, one per record, annotated with duration and roll-ups.
pub fn render_tree(roots: &[TreeNode]) -> String {
    render_tree_with(roots, &|_, label| label)
}

/// Like [`render_tree`], with each label passed through `paint` (for colour).
pub fn render_tree_with(roots: &[TreeNode], paint: Paint) -> String {
    let mut lines = Vec::new();
    for (i, root) in roots.iter().enumerate() {
        walk(root, "", i + 1 == roots.len(), paint, &mut lines);
    }
    lines.join("\n")
}

impl TreeNode {
    /// The node as JSON: level, message, roll-up and children.
    pub fn to_value(&self) -> Value {
        json!({
            "level": self.level,
            "message": self.message,
            "rollup": {
                "tokens_in": self.rollup.tokens_in as i64,
                "tokens_out": self.rollup.tokens_out as i64,
                "cost_usd": crate::jsonx::float_value(self.rollup.cost_usd),
            },
            "children": self.children.iter().map(TreeNode::to_value).collect::<Vec<_>>(),
        })
    }

    /// The node as the CLI and viewer show it: the whole record, the roll-up and the children.
    pub fn to_node_value(&self) -> Value {
        json!({
            "record": self.record,
            "rollup": {
                "tokens_in": self.rollup.tokens_in as i64,
                "tokens_out": self.rollup.tokens_out as i64,
                "cost_usd": crate::jsonx::float_value(self.rollup.cost_usd),
            },
            "children": self.children.iter().map(TreeNode::to_node_value).collect::<Vec<_>>(),
        })
    }
}
