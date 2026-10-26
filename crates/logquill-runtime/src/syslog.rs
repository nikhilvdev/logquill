//! The syslog transport, RFC 5424 over UDP or TCP (`docs/spec/05-runtime-and-transports.md`).
//!
//! One message per record, not batched: `<PRI>1 <timestamp> <hostname> <app-name> <pid> - -
//! <formatted>`. UDP sends one datagram per record; TCP connects lazily and frames messages with
//! a newline (RFC 6587 non-transparent framing), reconnecting after a failure.

use std::io::Write;
use std::net::{TcpStream, ToSocketAddrs, UdpSocket};

use serde_json::Value;

use crate::transport::{Formatter, Transport};

/// How messages travel.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SyslogProtocol {
    Udp,
    Tcp,
}

impl SyslogProtocol {
    /// `udp` or `tcp`.
    pub fn parse(name: &str) -> Result<SyslogProtocol, String> {
        match name {
            "udp" => Ok(SyslogProtocol::Udp),
            "tcp" => Ok(SyslogProtocol::Tcp),
            other => Err(format!(
                "SyslogTransport: protocol must be 'udp' or 'tcp', got {other}"
            )),
        }
    }
}

/// Settings for a [`SyslogTransport`].
#[derive(Clone, Debug)]
pub struct SyslogOptions {
    pub host: String,
    pub port: u16,
    pub protocol: SyslogProtocol,
    /// 1 (user) by default.
    pub facility: u8,
    /// The app name field; the record's logger name when absent.
    pub app_name: Option<String>,
    /// The hostname field; this machine's name when absent.
    pub hostname: Option<String>,
    pub formatter: Formatter,
}

impl Default for SyslogOptions {
    fn default() -> Self {
        SyslogOptions {
            host: "localhost".into(),
            port: 514,
            protocol: SyslogProtocol::Udp,
            facility: 1,
            app_name: None,
            hostname: None,
            formatter: Formatter::Json,
        }
    }
}

fn severity(level: &str) -> u8 {
    match level {
        "TRACE" | "DEBUG" => 7,
        "INFO" => 6,
        "WARN" => 4,
        "ERROR" => 3,
        "FATAL" => 2,
        _ => 6,
    }
}

/// Sends each record to a syslog server.
pub struct SyslogTransport {
    options: SyslogOptions,
    hostname: String,
    udp: Option<UdpSocket>,
    tcp: Option<TcpStream>,
}

/// A syslog header field may not hold spaces or non-printable characters.
fn field(text: &str, max: usize) -> String {
    let cleaned: String = text
        .chars()
        .filter(|c| c.is_ascii_graphic())
        .take(max)
        .collect();
    if cleaned.is_empty() {
        "-".to_string()
    } else {
        cleaned
    }
}

impl SyslogTransport {
    pub fn new(options: SyslogOptions) -> SyslogTransport {
        let hostname = options
            .hostname
            .clone()
            .unwrap_or_else(|| gethostname::gethostname().to_string_lossy().to_string());
        SyslogTransport {
            options,
            hostname: field(&hostname, 255),
            udp: None,
            tcp: None,
        }
    }

    /// The message for a formatted record, as it goes on the wire (without TCP framing).
    pub fn message(&self, formatted: &str, record: &Value) -> String {
        let level = record
            .get("level")
            .and_then(Value::as_str)
            .unwrap_or("INFO");
        let priority = u32::from(self.options.facility) * 8 + u32::from(severity(level));
        let app = self
            .options
            .app_name
            .as_deref()
            .or_else(|| record.get("logger").and_then(Value::as_str))
            .unwrap_or("-");
        let timestamp = record
            .get("timestamp")
            .and_then(Value::as_str)
            .unwrap_or("-");
        format!(
            "<{priority}>1 {} {} {} {} - - {formatted}",
            field(timestamp, 32),
            self.hostname,
            field(app, 48),
            std::process::id()
        )
    }

    fn address(&self) -> Result<std::net::SocketAddr, String> {
        (self.options.host.as_str(), self.options.port)
            .to_socket_addrs()
            .map_err(|e| {
                format!(
                    "SyslogTransport: couldn't resolve {} \u{2014} {e}",
                    self.options.host
                )
            })?
            .next()
            .ok_or_else(|| format!("SyslogTransport: {} has no address", self.options.host))
    }
}

impl Transport for SyslogTransport {
    fn format(&self, record: &Value) -> String {
        self.options.formatter.format(record)
    }

    fn write(&mut self, formatted: &str, record: &Value) -> Result<(), String> {
        let message = self.message(formatted, record);
        let target = self.address()?;
        match self.options.protocol {
            SyslogProtocol::Udp => {
                if self.udp.is_none() {
                    let bind = if target.is_ipv4() {
                        "0.0.0.0:0"
                    } else {
                        "[::]:0"
                    };
                    self.udp =
                        Some(UdpSocket::bind(bind).map_err(|e| format!("SyslogTransport: {e}"))?);
                }
                let socket = self.udp.as_ref().ok_or("SyslogTransport: no socket")?;
                socket
                    .send_to(message.as_bytes(), target)
                    .map(|_| ())
                    .map_err(|e| format!("SyslogTransport: couldn't send to {target} \u{2014} {e}"))
            }
            SyslogProtocol::Tcp => {
                if self.tcp.is_none() {
                    let stream =
                        TcpStream::connect_timeout(&target, std::time::Duration::from_secs(5))
                            .map_err(|e| {
                                format!(
                                    "SyslogTransport: couldn't connect to {target} \u{2014} {e}"
                                )
                            })?;
                    self.tcp = Some(stream);
                }
                let stream = self.tcp.as_mut().ok_or("SyslogTransport: no connection")?;
                let framed = format!("{message}\n");
                if let Err(e) = stream.write_all(framed.as_bytes()) {
                    self.tcp = None; // reconnect on the next record
                    return Err(format!(
                        "SyslogTransport: lost the connection to {target} \u{2014} {e}"
                    ));
                }
                Ok(())
            }
        }
    }

    fn close(&mut self) -> Result<(), String> {
        self.udp = None;
        self.tcp = None;
        Ok(())
    }
}
