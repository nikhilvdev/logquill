//! LogQuill core: pure logic shared by every language binding.
//!
//! No I/O and no threads live here; the clock and random source are injected so every
//! behaviour is deterministic under test. The behaviour to reproduce is written down in
//! `docs/spec/`.

mod canonical;
mod chain;
mod level;
mod llm;
mod policy;
mod record;
mod retry;
mod time;
mod toggle;

pub use canonical::{canonical_json, python_float_repr, Canon, CanonError};
pub use chain::{
    hash_record, sha256_hex, sign_head, verify_chain, verify_head_signature, verify_signed_chain,
    Chain, Verification, GENESIS_HASH,
};
pub use level::{Level, LevelError};
pub use llm::{build_llm_block, LlmBlock};
pub use policy::{
    apply_content_policy, keys_in_class, ContentPolicy, FieldClass, UnknownPolicy, CONTENT_FIELDS,
    FIELD_CLASSES, OFF_PLACEHOLDER, TRUNCATE_CHARS,
};
pub use record::{parse_record, Meta, Record, RecordError};
pub use retry::RetryTracker;
pub use time::{format_timestamp, Clock, SystemClock};
pub use toggle::Toggle;

/// The record shape this version writes (`schema/record.schema.json`).
pub const SCHEMA_VERSION: &str = "2.0";

/// What a record with no `schema_version` (anything written by logquill 1.x) is read as.
pub const LEGACY_SCHEMA_VERSION: &str = "1.0";
