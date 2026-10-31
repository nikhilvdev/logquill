//! Metrics backends for the SDK's `MetricsPlugin`: StatsD over UDP and the Prometheus pushgateway,
//! done in Rust. The plugin calls `increment` and `observe`; the pushgateway backend keeps its totals
//! in memory, bounded to a set number of series, and sends them on `flush`.

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Duration;

use logquill_runtime::{
    MetricsBackend, PushgatewayBackend as RuntimePushgateway, StatsdBackend as RuntimeStatsd,
    UreqPost,
};
use napi::{Error, Result};
use napi_derive::napi;

fn locked<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|p| p.into_inner())
}

/// Tags in a fixed (sorted) order, so the same tags always make the same series.
fn tag_list(tags: Option<HashMap<String, String>>) -> Vec<(String, String)> {
    let mut list: Vec<(String, String)> = tags.unwrap_or_default().into_iter().collect();
    list.sort();
    list
}

/// UDP, fire and forget: `name:1|c` and `name:value|h`, with `|#key:value` tags.
#[napi]
pub struct StatsdBackend {
    inner: Mutex<RuntimeStatsd>,
}

#[napi]
impl StatsdBackend {
    /// `host` defaults to `localhost`, `port` to 8125.
    #[napi(constructor)]
    pub fn new(host: Option<String>, port: Option<u32>, prefix: Option<String>) -> StatsdBackend {
        StatsdBackend {
            inner: Mutex::new(RuntimeStatsd::new(
                host.as_deref().unwrap_or("localhost"),
                port.map_or(8125, |p| p as u16),
                prefix.as_deref(),
            )),
        }
    }

    #[napi]
    pub fn increment(&self, name: String, tags: Option<HashMap<String, String>>) {
        locked(&self.inner).increment(&name, &tag_list(tags));
    }

    #[napi]
    pub fn observe(&self, name: String, value: f64, tags: Option<HashMap<String, String>>) {
        locked(&self.inner).observe(&name, value, &tag_list(tags));
    }
}

/// Options for `PushgatewayBackend`; every field but `gatewayUrl` is optional.
#[napi(object)]
pub struct PushgatewayConfig {
    pub gateway_url: String,
    /// The `job` label (default `logquill`).
    pub job_name: Option<String>,
    /// Seconds to wait for the gateway (default 5).
    pub timeout: Option<f64>,
    /// Distinct series kept before new ones are dropped (default 1000).
    pub max_series: Option<u32>,
}

/// Accumulates counters and histogram sum and count, and PUTs them to a pushgateway.
#[napi]
pub struct PushgatewayBackend {
    inner: Mutex<RuntimePushgateway>,
}

#[napi]
impl PushgatewayBackend {
    #[napi(constructor)]
    pub fn new(config: PushgatewayConfig) -> PushgatewayBackend {
        let mut backend = RuntimePushgateway::new(
            &config.gateway_url,
            config.job_name.as_deref().unwrap_or("logquill"),
            Duration::from_secs_f64(config.timeout.unwrap_or(5.0).max(0.0)),
            Box::new(UreqPost),
        );
        backend.max_series = config.max_series.map_or(1000, |n| n as usize);
        PushgatewayBackend {
            inner: Mutex::new(backend),
        }
    }

    #[napi]
    pub fn increment(&self, name: String, tags: Option<HashMap<String, String>>) {
        locked(&self.inner).increment(&name, &tag_list(tags));
    }

    #[napi]
    pub fn observe(&self, name: String, value: f64, tags: Option<HashMap<String, String>>) {
        locked(&self.inner).observe(&name, value, &tag_list(tags));
    }

    /// Sends every series accumulated so far; throws if the gateway refuses.
    #[napi]
    pub fn flush(&self) -> Result<()> {
        locked(&self.inner).flush().map_err(Error::from_reason)
    }

    /// How many samples were dropped because `maxSeries` distinct series were already kept.
    #[napi]
    pub fn dropped_samples(&self) -> f64 {
        locked(&self.inner).dropped_samples() as f64
    }
}
