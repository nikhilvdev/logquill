//! The common base for sinks that send in batches (`docs/spec/05-runtime-and-transports.md`).
//!
//! Records become items; the buffer is bounded by item count **and** estimated bytes, so a few
//! huge records cannot slip past a count limit. A failed send drops its batch (no retry), and a
//! send is never made with an empty batch.

use serde_json::Value;

use crate::transport::{Formatter, Transport};

/// Where a finished batch goes.
pub trait BatchSender<T>: Send {
    /// Sends the batch. An `Err` is reported and the batch dropped.
    fn send(&mut self, batch: &[T]) -> Result<(), String>;

    /// Releases resources when the transport closes.
    fn close(&mut self) {}
}

/// Turns a formatted line and its record into an item and its estimated size in bytes.
pub type ToItem<T> = Box<dyn Fn(&str, &Value) -> (T, usize) + Send>;

/// Buffers items and flushes at `max_records` or `max_bytes`, whichever comes first.
pub struct Batching<T> {
    formatter: Formatter,
    to_item: ToItem<T>,
    sender: Box<dyn BatchSender<T>>,
    max_records: usize,
    max_bytes: usize,
    items: Vec<T>,
    bytes: usize,
}

impl<T: Send> Batching<T> {
    /// Defaults: 100 records and 1,000,000 bytes.
    pub fn new(
        formatter: Formatter,
        to_item: ToItem<T>,
        sender: Box<dyn BatchSender<T>>,
        max_records: usize,
        max_bytes: usize,
    ) -> Batching<T> {
        Batching {
            formatter,
            to_item,
            sender,
            max_records: max_records.max(1),
            max_bytes: max_bytes.max(1),
            items: Vec::new(),
            bytes: 0,
        }
    }

    /// Items waiting to be sent.
    pub fn pending(&self) -> usize {
        self.items.len()
    }
}

impl<T: Send> Transport for Batching<T> {
    fn format(&self, record: &Value) -> String {
        self.formatter.format(record)
    }

    fn write(&mut self, formatted: &str, record: &Value) -> Result<(), String> {
        let (item, size) = (self.to_item)(formatted, record);
        self.items.push(item);
        self.bytes += size;
        if self.items.len() >= self.max_records || self.bytes >= self.max_bytes {
            return self.flush();
        }
        Ok(())
    }

    fn flush(&mut self) -> Result<(), String> {
        if self.items.is_empty() {
            return Ok(());
        }
        let batch = std::mem::take(&mut self.items);
        self.bytes = 0;
        self.sender.send(&batch)
    }

    fn close(&mut self) -> Result<(), String> {
        let result = self.flush();
        self.sender.close();
        result
    }
}
