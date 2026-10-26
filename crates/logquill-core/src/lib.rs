//! LogQuill core: pure logic shared by every language binding.
//!
//! No I/O and no threads live here; the clock and random source are injected so every
//! behaviour is deterministic under test. The behaviour to reproduce is written down in
//! `docs/spec/`.

mod canonical;
mod chain;
mod format;
mod jsonx;
mod level;
mod llm;
mod ordered;
mod pipeline;
mod plugins;
mod policy;
mod record;
mod redact;
mod retry;
mod rows;
mod semconv;
mod time;
mod toggle;
mod trace;
mod tree;

pub use canonical::{canonical_json, python_float_repr, Canon, CanonError};
pub use chain::{
    hash_record, sha256_hex, sign_head, verify_chain, verify_head_signature, verify_signed_chain,
    Chain, Verification, GENESIS_HASH,
};
pub use format::{format_json, format_logfmt, format_text, parse_logfmt};
pub use jsonx::json_line;
pub use level::{Level, LevelError};
pub use llm::{build_llm_block, LlmBlock};
pub use pipeline::{Call, Dropped, FnPlugin, Outcome, Pipeline, Plugin, SharedChain, Verdict};
pub use plugins::{
    AdaptiveOptions, AdaptiveSampling, Alert, AlertDedupe, AlertOptions, Decision, FlightOptions,
    FlightRecorder, RateKey, RateLimit, RunStamp, RunSummary, Sampling, SamplingOptions,
    SummaryOptions,
};
pub use policy::{
    apply_content_policy, keys_in_class, ContentPolicy, FieldClass, UnknownPolicy, CONTENT_FIELDS,
    FIELD_CLASSES, OFF_PLACEHOLDER, TRUNCATE_CHARS,
};
pub use record::{parse_record, Meta, Record, RecordError};
pub use redact::{KeyRedactor, PiiRedactor, DEFAULT_KEYS, MAX_DEPTH};
pub use retry::RetryTracker;
pub use rows::{
    create_table_sql, graph_row, sql_row, Dialect, GraphKeys, CYPHER, LEAF_PLACEHOLDER,
};
pub use semconv::{
    capture_content_enabled, epoch_ns, export_spec, is_llm_record, is_span_record, is_tool_record,
    llm_span, log_attributes, resolve_convention, span_spec, to_ecs_document, tool_span,
    Convention, Name, SpanKind, SpanSpec, SpecOptions, CAPTURE_ENV, CONVENTION_VERSION, OPT_IN_ENV,
};
pub use time::{format_timestamp, Clock, SystemClock};
pub use toggle::Toggle;
pub use trace::{parse_trace_header, span_id_for, trace_id_for, trace_id_from_parent};
pub use tree::{build_trace, render_tree, Rollup, Trace, TreeNode};

/// The record shape this version writes (`schema/record.schema.json`).
pub const SCHEMA_VERSION: &str = "2.0";

/// What a record with no `schema_version` (anything written by logquill 1.x) is read as.
pub const LEGACY_SCHEMA_VERSION: &str = "1.0";
