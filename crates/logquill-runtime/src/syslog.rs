//! The syslog transport, RFC 5424 over UDP or TCP (`docs/spec/05-runtime-and-transports.md`).
//!
//! One message per record, not batched: `<PRI>1 <timestamp> <hostname> <app-name> <pid> - -
//! <formatted>`. UDP sends one datagram per record; TCP connects lazily and frames messages with
//! a newline (RFC 6587 non-transparent framing), reconnecting after a failure.
//!
//! The server's address is looked up once and kept until a send fails. A failed lookup or connect
//! is reported once and then records are dropped quietly for a growing pause (1 s up to 60 s), so a
//! down server costs one slow attempt per pause, not one per record. TCP writes time out.

use std::io::Write;
use std::net::{SocketAddr, TcpStream, ToSocketAddrs, UdpSocket};
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::transport::{Formatter, Transport};

const FIRST_PAUSE: Duration = Duration::from_secs(1);
const LONGEST_PAUSE: Duration = Duration::from_secs(60);

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
    /// How long a TCP connect may take. Default 5 seconds.
    pub connect_timeout: Duration,
    /// How long a TCP write may block on a server that is not reading. Default 5 seconds.
    pub write_timeout: Duration,
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
            connect_timeout: Duration::from_secs(5),
            write_timeout: Duration::from_secs(5),
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

/// Turns a host name and port into addresses; replaceable so a test can count lookups.
pub type Resolver = Box<dyn Fn(&str, u16) -> std::io::Result<Vec<SocketAddr>> + Send>;

fn system_resolver() -> Resolver {
    Box::new(|host, port| Ok((host, port).to_socket_addrs()?.collect()))
}

/// Sends each record to a syslog server.
pub struct SyslogTransport {
    options: SyslogOptions,
    hostname: String,
    resolver: Resolver,
    resolved: Option<SocketAddr>,
    udp: Option<UdpSocket>,
    tcp: Option<TcpStream>,
    /// While set and in the future, records are dropped without trying the server.
    pause_until: Option<Instant>,
    pause: Duration,
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
        SyslogTransport::with_resolver(options, system_resolver())
    }

    /// Like [`SyslogTransport::new`] with the address lookup supplied by the caller.
    pub fn with_resolver(options: SyslogOptions, resolver: Resolver) -> SyslogTransport {
        let hostname = options
            .hostname
            .clone()
            .unwrap_or_else(|| gethostname::gethostname().to_string_lossy().to_string());
        SyslogTransport {
            options,
            hostname: field(&hostname, 255),
            resolver,
            resolved: None,
            udp: None,
            tcp: None,
            pause_until: None,
            pause: FIRST_PAUSE,
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

    /// The server's address, looked up on first use and then remembered.
    fn address(&mut self) -> Result<SocketAddr, String> {
        if let Some(address) = self.resolved {
            return Ok(address);
        }
        let host = &self.options.host;
        let found = (self.resolver)(host, self.options.port)
            .map_err(|e| format!("SyslogTransport: couldn't resolve {host} \u{2014} {e}"))?
            .into_iter()
            .next()
            .ok_or_else(|| format!("SyslogTransport: {host} has no address"))?;
        self.resolved = Some(found);
        Ok(found)
    }

    /// Starts (or lengthens) a pause and says so in the error that is reported once.
    fn back_off(&mut self, error: String) -> String {
        let wait = self.pause;
        self.pause_until = Some(Instant::now() + wait);
        self.pause = (wait * 2).min(LONGEST_PAUSE);
        self.resolved = None;
        format!(
            "{error}; records are dropped for {}s before the next attempt",
            wait.as_secs()
        )
    }
}

impl Transport for SyslogTransport {
    fn format(&self, record: &Value) -> String {
        self.options.formatter.format(record)
    }

    fn write(&mut self, formatted: &str, record: &Value) -> Result<(), String> {
        if self.tcp.is_none() && self.pause_until.is_some_and(|until| Instant::now() < until) {
            return Ok(()); // the failure that began this pause was already reported
        }
        let message = self.message(formatted, record);
        let target = match self.address() {
            Ok(target) => target,
            Err(error) => return Err(self.back_off(error)),
        };
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
                    .map_err(|e| {
                        self.resolved = None; // the address may have changed; look it up again
                        format!("SyslogTransport: couldn't send to {target} \u{2014} {e}")
                    })
            }
            SyslogProtocol::Tcp => {
                if self.tcp.is_none() {
                    match TcpStream::connect_timeout(&target, self.options.connect_timeout) {
                        Ok(stream) => {
                            let _ = stream.set_write_timeout(Some(self.options.write_timeout));
                            self.tcp = Some(stream);
                            self.pause_until = None;
                            self.pause = FIRST_PAUSE;
                        }
                        Err(e) => {
                            let error = format!(
                                "SyslogTransport: couldn't connect to {target} \u{2014} {e}"
                            );
                            return Err(self.back_off(error));
                        }
                    }
                }
                let stream = self.tcp.as_mut().ok_or("SyslogTransport: no connection")?;
                let framed = format!("{message}\n");
                if let Err(e) = stream.write_all(framed.as_bytes()) {
                    self.tcp = None; // reconnect on the next record
                    self.resolved = None;
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
