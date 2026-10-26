//! LogQuill runtime: the bounded queue and worker thread, and the native transports.
//!
//! Behaviour is specified in `docs/spec/05-runtime-and-transports.md`. The pure logic (records,
//! formatting, redaction, the pipeline) lives in `logquill-core`; this crate owns the threads,
//! files and sockets.

mod batching;
mod console;
mod dispatcher;
mod file;
mod transport;
mod worker;

pub use batching::{BatchSender, Batching, ToItem};
pub use console::{colour_default, Console};
pub use dispatcher::Dispatcher;
pub use file::{FileOptions, FileTransport};
pub use transport::{Formatter, Transport};
pub use worker::{Backpressure, Stats, Worker, WorkerOptions};
