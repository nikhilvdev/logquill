//! LogQuill runtime: the bounded queue and worker thread, and the native transports.
//!
//! Behaviour is specified in `docs/spec/05-runtime-and-transports.md`. The pure logic (records,
//! formatting, redaction, the pipeline) lives in `logquill-core`; this crate owns the threads,
//! files and sockets.

mod alerts;
mod batching;
mod console;
mod dispatcher;
mod fernet;
mod file;
mod http;
mod metrics;
mod otlp;
mod shutdown;
mod sqlite;
mod syslog;
mod transport;
mod watchers;
mod worker;

pub use alerts::{
    pagerduty_event, slack_text, AlertSender, Alerting, EmailOptions, EmailSender, PagerDutySender,
    SlackSender, PAGERDUTY_URL,
};
pub use batching::{BatchSender, Batching, ToItem};
pub use console::{colour_default, Console};
pub use dispatcher::Dispatcher;
pub use fernet::{generate_fernet_key, Fernet};
pub use file::{Compression, FileOptions, FileTransport};
pub use http::{
    app_insights_transport, datadog_transport, datadog_url, elasticsearch_transport,
    http_transport, new_relic_transport, retry_after_seconds, system_epoch_clock, EpochClock,
    HttpOptions, HttpPost, HttpResponse, UreqPost,
};
pub use metrics::{
    MetricsBackend, MetricsPlugin, OtlpMetricsBackend, PushgatewayBackend, StatsdBackend,
};
pub use otlp::{
    any_value, log_record_json, otlp_log_transport, otlp_trace_transport, span_json, OtlpOptions,
};
pub use shutdown::{close_all, register, with_flush};
pub use sqlite::{sqlite_transport, SqliteOptions};
pub use syslog::{SyslogOptions, SyslogProtocol, SyslogTransport};
pub use transport::{Formatter, Transport};
pub use watchers::{parse_level_setting, LevelSource, LevelWatcher};
pub use worker::{Backpressure, Stats, Worker, WorkerOptions};
