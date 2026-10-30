import CLogQuill
import Foundation

/// Log levels, in order of severity.
public enum Level: String, Sendable, CaseIterable {
    case trace, debug, info, warn, error, fatal
}

/// A transport written in Swift: called on the logging thread with the finished, already-redacted record, after the Rust
/// transports have been handed it. Keep `write` quick, or queue inside it.
public protocol HostTransport: AnyObject {
    /// Writes one record, as its JSON line and parsed.
    func write(line: String, record: [String: Any])
    /// Sends anything buffered. The default does nothing.
    func flush()
    /// Called once from ``Logger/close(timeout:)``. The default does nothing.
    func close()
}

public extension HostTransport {
    func flush() {}
    func close() {}
}

/// Builders for ``LoggerOptions/plugins``: plugins that run in Rust, in order.
public enum Plugins {
    /// Masks the values of keys (case-insensitively, at every depth). Defaults to password, token, secret, api_key, authorization.
    public static func redact(keys: [String]? = nil, classes: [String]? = nil, replacement: String? = nil) -> [String: Any] {
        var plugin: [String: Any] = ["type": "redact"]
        if let keys { plugin["keys"] = keys }
        if let classes { plugin["classes"] = classes }
        if let replacement { plugin["replacement"] = replacement }
        return plugin
    }

    /// Masks emails, card numbers, phone numbers, SSNs and API keys inside any string of the record's meta.
    public static func pii(replacement: String? = nil) -> [String: Any] {
        var plugin: [String: Any] = ["type": "pii"]
        if let replacement { plugin["replacement"] = replacement }
        return plugin
    }

    /// Keeps about `rate` (0 to 1) of the records, chosen at random.
    public static func sampling(rate: Double) -> [String: Any] {
        ["type": "sampling", "rate": rate]
    }

    /// Drops records once more than `maxRecords` share a key inside `perSeconds`. `key` is "logger_level" (the default), "logger" or "message".
    public static func rateLimit(maxRecords: Int, perSeconds: Double, key: String? = nil) -> [String: Any] {
        var plugin: [String: Any] = ["type": "rate_limit", "max_records": maxRecords, "per_seconds": perSeconds]
        if let key { plugin["key"] = key }
        return plugin
    }

    /// Keeps errors and slow spans, samples the rest at a rate that follows demand, and caps the bytes written per window.
    /// An option left nil keeps the core's default.
    public static func adaptiveSampling(
        baseRate: Double? = nil, minRate: Double? = nil, maxRate: Double? = nil, keepAt: String? = nil,
        slowMs: Double? = nil, maxBytesPerSecond: Int? = nil, windowSeconds: Double? = nil
    ) -> [String: Any] {
        var plugin: [String: Any] = ["type": "adaptive_sampling"]
        if let baseRate { plugin["base_rate"] = baseRate }
        if let minRate { plugin["min_rate"] = minRate }
        if let maxRate { plugin["max_rate"] = maxRate }
        if let keepAt { plugin["keep_at"] = keepAt }
        if let slowMs { plugin["slow_ms"] = slowMs }
        if let maxBytesPerSecond { plugin["max_bytes_per_second"] = maxBytesPerSecond }
        if let windowSeconds { plugin["window_seconds"] = windowSeconds }
        return plugin
    }
}

/// Builders for ``LoggerOptions/transports``: transports written by Rust on a background thread.
public enum Transports {
    /// Writes each record to standard output (ERROR and FATAL to standard error).
    public static func console(format: String = "json", colour: Bool? = nil) -> [String: Any] {
        var transport: [String: Any] = ["type": "console", "format": format]
        if let colour { transport["colour"] = colour }
        return transport
    }

    /// Writes each record to a file, rotating by size.
    public static func file(
        path: String, format: String = "json", maxBytes: Int? = nil, backupCount: Int? = nil, compression: String? = nil
    ) -> [String: Any] {
        var transport: [String: Any] = ["type": "file", "path": path, "format": format]
        if let maxBytes { transport["max_bytes"] = maxBytes }
        if let backupCount { transport["backup_count"] = backupCount }
        if let compression { transport["compression"] = compression }
        return transport
    }
}

/// What a ``Logger`` is built from. Every option has a default.
public struct LoggerOptions {
    public var name = "app"
    public var level = Level.info
    /// Plugins run in Rust, in order: ``Plugins/redact(keys:classes:replacement:)``, ``Plugins/pii(replacement:)``.
    public var plugins: [[String: Any]] = []
    /// Rust transports: ``Transports/console(format:colour:)``, ``Transports/file(path:format:maxBytes:backupCount:compression:)``.
    public var transports: [[String: Any]] = []
    /// Swift transports, called on the logging thread with the finished record.
    public var hostTransports: [HostTransport] = []
    /// `off` (default: prompt, completion and tool content is withheld), `hash`, `truncate` or `full`.
    public var contentPolicy: String?
    /// Chain each record as it is written, so an edited or removed line can be found.
    public var tamperEvident = false
    /// The queue in front of the Rust transports: `["max_size": 10000, "backpressure": "drop_oldest"]`.
    public var queue: [String: Any]?

    public init() {}
}

/// A logger on the Rust core: the level filter, redaction and the hash chain run in Rust before anything is written.
///
///     var options = LoggerOptions()
///     options.plugins = [Plugins.redact(), Plugins.pii()]
///     options.transports = [Transports.file(path: "web.log")]
///     let log = try Logger(options)
///     log.info("signed in", ["user": "alice@example.com", "password": "hunter2"])
///
/// `init` throws ``LogQuillError`` for a config the core rejects; a log call never throws. Safe to use from many threads.
public final class Logger: @unchecked Sendable {
    private let handle: OpaquePointer
    private let host: [HostTransport]
    /// The logger's name (`web`, `web.db`, ...).
    public let name: String

    /// Builds a logger from `options`.
    public init(_ options: LoggerOptions = LoggerOptions()) throws {
        var config: [String: Any] = [
            "name": options.name, "level": options.level.rawValue, "plugins": options.plugins, "transports": options.transports,
            "tamper_evident": options.tamperEvident,
        ]
        if let policy = options.contentPolicy { config["content_policy"] = policy }
        if let queue = options.queue { config["queue"] = queue }
        var error: UnsafeMutablePointer<CChar>?
        guard let handle = lq_logger_new(JSONSafe.encode(config), &error) else { throw failure(error, "the logger could not be created") }
        self.handle = handle
        self.host = options.hostTransports
        self.name = options.name
    }

    private init(handle: OpaquePointer, host: [HostTransport], name: String) {
        self.handle = handle
        self.host = host
        self.name = name
    }

    deinit { lq_logger_free(handle) }

    /// A logger named `<name>.<suffix>` with `fixedMeta` on every record, sharing this logger's level and transports.
    public func child(_ suffix: String, _ fixedMeta: [String: Any]? = nil) throws -> Logger {
        var error: UnsafeMutablePointer<CChar>?
        guard let child = lq_logger_child(handle, suffix, JSONSafe.encodeObject(fixedMeta), &error) else { throw failure(error) }
        return Logger(handle: child, host: host, name: "\(name).\(suffix)")
    }

    /// Logs at the level of the method's name. Returns `true` when the record was kept and `false` when it was filtered. Like
    /// every log method it never throws: a value JSON cannot carry is turned into text, and a failure is written to standard error.
    @discardableResult public func trace(_ message: String, _ meta: [String: Any]? = nil, llm: [String: Any]? = nil) -> Bool { log(.trace, message, meta, llm: llm) }
    @discardableResult public func debug(_ message: String, _ meta: [String: Any]? = nil, llm: [String: Any]? = nil) -> Bool { log(.debug, message, meta, llm: llm) }
    @discardableResult public func info(_ message: String, _ meta: [String: Any]? = nil, llm: [String: Any]? = nil) -> Bool { log(.info, message, meta, llm: llm) }
    @discardableResult public func warn(_ message: String, _ meta: [String: Any]? = nil, llm: [String: Any]? = nil) -> Bool { log(.warn, message, meta, llm: llm) }
    @discardableResult public func error(_ message: String, _ meta: [String: Any]? = nil, llm: [String: Any]? = nil) -> Bool { log(.error, message, meta, llm: llm) }
    @discardableResult public func fatal(_ message: String, _ meta: [String: Any]? = nil, llm: [String: Any]? = nil) -> Bool { log(.fatal, message, meta, llm: llm) }

    /// The call behind the level methods. `meta` becomes the record's `meta`, `llm` its LLM-call block (`model`, `tokens_in`, ...).
    @discardableResult public func log(_ level: Level, _ message: String, _ meta: [String: Any]? = nil, llm: [String: Any]? = nil) -> Bool {
        logRecord(level, message, meta, llm: llm) != nil
    }

    /// Like ``log(_:_:_:llm:)``, returning the finished record as JSON text, or nil when it was filtered.
    public func logRecord(_ level: Level, _ message: String, _ meta: [String: Any]? = nil, llm: [String: Any]? = nil) -> String? {
        let context = LogContext.fields
        let merged = context.isEmpty ? meta : context.merging(meta ?? [:]) { _, own in own }
        var record: UnsafeMutablePointer<CChar>?
        var error: UnsafeMutablePointer<CChar>?
        let text = message.contains("\0") ? message.replacingOccurrences(of: "\0", with: "\u{2400}") : message
        let code = lq_log(handle, level.rawValue, nil, text, JSONSafe.encodeObject(merged), JSONSafe.encodeObject(llm), &record, &error)
        if code < 0 {
            FileHandle.standardError.write(Data("logquill: \(failure(error).message)\n".utf8))
            return nil
        }
        guard code == 1, let record else { return nil }
        let line = take(record)
        deliver(line)
        return line
    }

    private func deliver(_ line: String) {
        guard !host.isEmpty else { return }
        let parsed = (try? JSONSerialization.jsonObject(with: Data(line.utf8))) as? [String: Any] ?? [:]
        for transport in host { transport.write(line: line, record: parsed) }
    }

    /// Changes the level for this logger, its parent and its children.
    public func setLevel(_ level: Level) throws {
        var error: UnsafeMutablePointer<CChar>?
        if lq_logger_set_level(handle, level.rawValue, &error) < 0 { throw failure(error) }
    }

    /// Waits for queued records to be written (up to `timeout`, default 5 s) and flushes Swift transports. True when done in time.
    @discardableResult public func flush(timeout: TimeInterval = 5) -> Bool {
        host.forEach { $0.flush() }
        return lq_logger_flush(handle, UInt32(max(0, min(timeout * 1000, Double(UInt32.max))))) == 1
    }

    /// Flushes, stops the background thread and closes Swift transports. Call once, on shutdown.
    @discardableResult public func close(timeout: TimeInterval = 5) -> Bool {
        let done = lq_logger_close(handle, UInt32(max(0, min(timeout * 1000, Double(UInt32.max))))) == 1
        host.forEach { $0.close() }
        return done
    }
}
