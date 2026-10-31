using System.Text.Json;

namespace LogQuill;

/// <summary>Log levels, in order of severity.</summary>
public enum Level
{
    /// <summary>Finest detail.</summary>
    Trace,
    /// <summary>Diagnostic detail.</summary>
    Debug,
    /// <summary>Normal operation.</summary>
    Info,
    /// <summary>Something unexpected that was handled.</summary>
    Warn,
    /// <summary>An operation failed.</summary>
    Error,
    /// <summary>The application cannot continue.</summary>
    Fatal,
}

/// <summary>
/// A transport written in .NET: called on the logging thread with the finished, already-redacted record, after the Rust
/// transports have been handed it. Keep <see cref="Write"/> quick, or queue inside it.
/// </summary>
public interface ITransport
{
    /// <summary>Writes one record, as its JSON line and parsed.</summary>
    void Write(string line, JsonElement record);

    /// <summary>Sends anything buffered. The default does nothing.</summary>
    void Flush() { }

    /// <summary>Called once from <see cref="Logger.Close"/>. The default does nothing.</summary>
    void Close() { }
}

/// <summary>The queue between a log call and the Rust transports.</summary>
/// <param name="MaxSize">Most records queued at once (default 10000).</param>
/// <param name="Backpressure"><c>drop_oldest</c> (default), <c>drop_newest</c> or <c>block</c>, for when the queue is full.</param>
public sealed record QueueOptions(int? MaxSize = null, string? Backpressure = null);

/// <summary>What a <see cref="Logger"/> is built from. Every option has a default.</summary>
public sealed class LoggerOptions
{
    /// <summary>The logger's name (default <c>app</c>).</summary>
    public string Name { get; init; } = "app";

    /// <summary>The lowest level that is kept (default <see cref="LogQuill.Level.Info"/>).</summary>
    public Level Level { get; init; } = Level.Info;

    /// <summary>Plugins run in Rust, in order: <see cref="PluginConfig.Redact"/>, <see cref="PluginConfig.Pii"/>.</summary>
    public IList<object> Plugins { get; init; } = new List<object>();

    /// <summary>Rust transports, written on a background thread: <see cref="TransportConfig.Console"/>, <see cref="TransportConfig.File"/>.</summary>
    public IList<object> Transports { get; init; } = new List<object>();

    /// <summary>.NET transports, called on the logging thread with the finished record.</summary>
    public IList<ITransport> HostTransports { get; init; } = new List<ITransport>();

    /// <summary><c>off</c> (default: prompt, completion and tool content is withheld), <c>hash</c>, <c>truncate</c> or <c>full</c>.</summary>
    public string? ContentPolicy { get; init; }

    /// <summary>Chain each record as it is written, so an edited or removed line can be found (<see cref="Core.VerifyChain"/>).</summary>
    public bool TamperEvident { get; init; }

    /// <summary>The queue in front of the Rust transports.</summary>
    public QueueOptions? Queue { get; init; }
}

/// <summary>Builders for <see cref="LoggerOptions.Plugins"/>.</summary>
public static class PluginConfig
{
    /// <summary>Masks the values of keys (case-insensitively, at every depth). Defaults to password, token, secret, api_key, authorization.</summary>
    public static object Redact(IEnumerable<string>? keys = null, IEnumerable<string>? classes = null, string? replacement = null) =>
        Build("redact", ("keys", keys?.ToArray()), ("classes", classes?.ToArray()), ("replacement", replacement));

    /// <summary>Masks emails, card numbers, phone numbers, SSNs and API keys inside any string of the record's meta.</summary>
    public static object Pii(string? replacement = null) => Build("pii", ("replacement", replacement));

    /// <summary>Keeps about <paramref name="rate"/> (0 to 1) of the records, chosen at random.</summary>
    public static object Sampling(double rate) => Build("sampling", ("rate", rate));

    /// <summary>Drops records once more than <paramref name="maxRecords"/> share a key inside <paramref name="perSeconds"/>. <paramref name="key"/> is "logger_level" (the default), "logger" or "message".</summary>
    public static object RateLimit(int maxRecords, double perSeconds, string? key = null) =>
        Build("rate_limit", ("max_records", maxRecords), ("per_seconds", perSeconds), ("key", key));

    /// <summary>Keeps errors and slow spans, samples the rest at a rate that follows demand, and caps the bytes written per window. An option left null keeps the core's default.</summary>
    public static object AdaptiveSampling(
        double? baseRate = null, double? minRate = null, double? maxRate = null, string? keepAt = null,
        double? slowMs = null, long? maxBytesPerSecond = null, double? windowSeconds = null) =>
        Build("adaptive_sampling", ("base_rate", baseRate), ("min_rate", minRate), ("max_rate", maxRate), ("keep_at", keepAt),
            ("slow_ms", slowMs), ("max_bytes_per_second", maxBytesPerSecond), ("window_seconds", windowSeconds));

    internal static Dictionary<string, object?> Build(string type, params (string Key, object? Value)[] options)
    {
        var map = new Dictionary<string, object?> { ["type"] = type };
        foreach (var (key, value) in options)
        {
            if (value is not null)
            {
                map[key] = value;
            }
        }

        return map;
    }
}

/// <summary>Builders for <see cref="LoggerOptions.Transports"/>.</summary>
public static class TransportConfig
{
    /// <summary>Writes each record to standard output (ERROR and FATAL to standard error).</summary>
    public static object Console(string format = "json", bool? colour = null) =>
        PluginConfig.Build("console", ("format", format), ("colour", colour));

    /// <summary>Writes each record to a file, rotating by size.</summary>
    public static object File(
        string path, string format = "json", long? maxBytes = null, int? backupCount = null, string? compression = null,
        string? encryptKey = null) =>
        PluginConfig.Build("file", ("path", path), ("format", format), ("max_bytes", maxBytes), ("backup_count", backupCount),
            ("compression", compression), ("encrypt_key", encryptKey));
}

/// <summary>
/// A logger on the Rust core: the level filter, redaction and the hash chain run in Rust before anything is written.
/// <code>
/// using var log = new Logger(new LoggerOptions {
///     Plugins = { PluginConfig.Redact(), PluginConfig.Pii() },
///     Transports = { TransportConfig.File("web.log") },
/// });
/// log.Info("signed in", new { User = "alice@example.com", Password = "hunter2" });
/// </code>
/// The constructor throws <see cref="LogQuillConfigException"/> for a config the core rejects; a log call never throws.
/// Safe to use from many threads.
/// </summary>
public sealed class Logger : IDisposable
{
    private readonly LoggerHandle _handle;
    private readonly IReadOnlyList<ITransport> _host;

    /// <summary>This logger's name (<c>web</c>, <c>web.db</c>, ...).</summary>
    public string Name { get; }

    /// <summary>Builds a logger from <paramref name="options"/>.</summary>
    public Logger(LoggerOptions? options = null)
    {
        options ??= new LoggerOptions();
        var config = new Dictionary<string, object?>
        {
            ["name"] = options.Name,
            ["level"] = LevelName(options.Level),
            ["plugins"] = options.Plugins,
            ["transports"] = options.Transports,
            ["content_policy"] = options.ContentPolicy,
            ["tamper_evident"] = options.TamperEvident,
            ["queue"] = options.Queue is null
                ? null
                : PluginConfig.Build("queue", ("max_size", options.Queue.MaxSize), ("backpressure", options.Queue.Backpressure))
                    .Where(entry => entry.Key != "type").ToDictionary(entry => entry.Key, entry => entry.Value),
        };
        var raw = Native.lq_logger_new(Json.Dump(config), out var error);
        if (raw == IntPtr.Zero)
        {
            throw new LogQuillConfigException(Native.ErrorText(error, "the logger could not be created"));
        }

        _handle = new LoggerHandle(raw);
        _host = options.HostTransports.ToArray();
        Name = options.Name;
    }

    private Logger(LoggerHandle handle, string name, IReadOnlyList<ITransport> host)
    {
        _handle = handle;
        Name = name;
        _host = host;
    }

    /// <summary>A logger named <c>&lt;name&gt;.&lt;suffix&gt;</c> with <paramref name="fixedMeta"/> on every record, sharing this logger's level and transports.</summary>
    public Logger Child(string suffix, object? fixedMeta = null)
    {
        var raw = Native.lq_logger_child(_handle, suffix, Json.DumpObject(fixedMeta), out var error);
        if (raw == IntPtr.Zero)
        {
            throw new LogQuillException(Native.ErrorText(error));
        }

        return new Logger(new LoggerHandle(raw), $"{Name}.{suffix}", _host);
    }

    /// <summary>Logs at <see cref="Level.Trace"/>. Returns true when the record was kept, false when it was filtered.</summary>
    public bool Trace(string message, object? meta = null, object? llm = null) => Log(Level.Trace, message, meta, llm);

    /// <summary>Logs at <see cref="Level.Debug"/>.</summary>
    public bool Debug(string message, object? meta = null, object? llm = null) => Log(Level.Debug, message, meta, llm);

    /// <summary>Logs at <see cref="Level.Info"/>.</summary>
    public bool Info(string message, object? meta = null, object? llm = null) => Log(Level.Info, message, meta, llm);

    /// <summary>Logs at <see cref="Level.Warn"/>.</summary>
    public bool Warn(string message, object? meta = null, object? llm = null) => Log(Level.Warn, message, meta, llm);

    /// <summary>Logs at <see cref="Level.Error"/>.</summary>
    public bool Error(string message, object? meta = null, object? llm = null) => Log(Level.Error, message, meta, llm);

    /// <summary>Logs at <see cref="Level.Fatal"/>.</summary>
    public bool Fatal(string message, object? meta = null, object? llm = null) => Log(Level.Fatal, message, meta, llm);

    /// <summary>
    /// One log call. <paramref name="meta"/> is an anonymous object or dictionary (properties become snake_case keys);
    /// <paramref name="llm"/> is an LLM-call block (<c>model</c>, <c>tokens_in</c>, ...). Never throws: a value JSON cannot carry
    /// is turned into text, and a failure is written to standard error. Returns whether the record was kept.
    /// </summary>
    public bool Log(Level level, string message, object? meta = null, object? llm = null) => LogRecord(level, message, meta, llm) is not null;

    /// <summary>Like <see cref="Log"/>, returning the finished record as JSON text, or null when it was filtered.</summary>
    public string? LogRecord(Level level, string message, object? meta = null, object? llm = null)
    {
        try
        {
            var code = Native.lq_log(
                _handle, LevelName(level), null, Json.Scrub(message), MetaJson(meta), llm is null ? null : Json.DumpObject(llm),
                out var record, out var error);
            if (code < 0)
            {
                return Failed(Native.ErrorText(error));
            }

            var text = record == IntPtr.Zero ? null : Native.Take(record);
            if (code == 0 || text is null)
            {
                return null;
            }

            Deliver(text);
            return text;
        }
        catch (Exception exception) when (exception is not OutOfMemoryException)
        {
            return Failed(exception.Message);
        }
    }

    /// <summary>Changes the level for this logger, its parent and its children.</summary>
    public void SetLevel(Level level)
    {
        if (Native.lq_logger_set_level(_handle, LevelName(level), out var error) < 0)
        {
            throw new ArgumentException(Native.ErrorText(error), nameof(level));
        }
    }

    /// <summary>Waits for queued records to be written (up to <paramref name="timeout"/>, default 5 s) and flushes .NET transports. True when done in time.</summary>
    public bool Flush(TimeSpan? timeout = null)
    {
        foreach (var transport in _host)
        {
            transport.Flush();
        }

        return Native.lq_logger_flush(_handle, Milliseconds(timeout)) == 1;
    }

    /// <summary>Flushes, stops the background thread and closes .NET transports. Call once, on shutdown.</summary>
    public bool Close(TimeSpan? timeout = null)
    {
        var done = Native.lq_logger_close(_handle, Milliseconds(timeout)) == 1;
        foreach (var transport in _host)
        {
            transport.Close();
        }

        return done;
    }

    /// <summary>Flushes what is queued and releases the native logger.</summary>
    public void Dispose() => _handle.Dispose();

    private static string LevelName(Level level) => level.ToString().ToLowerInvariant();

    private static uint Milliseconds(TimeSpan? timeout) => (uint)Math.Clamp((timeout ?? TimeSpan.FromSeconds(5)).TotalMilliseconds, 0, uint.MaxValue);

    private static string? MetaJson(object? meta)
    {
        var context = LogContext.Fields;
        if (context.Count == 0)
        {
            return Json.DumpObject(meta);
        }

        var merged = new Dictionary<string, object?>(context);
        var own = Json.DumpObject(meta);
        if (own is not null)
        {
            using var document = JsonDocument.Parse(own);
            foreach (var property in document.RootElement.EnumerateObject())
            {
                merged[property.Name] = property.Value.Clone();
            }
        }

        return Json.Dump(merged);
    }

    private void Deliver(string line)
    {
        if (_host.Count == 0)
        {
            return;
        }

        using var document = JsonDocument.Parse(line);
        foreach (var transport in _host)
        {
            try
            {
                transport.Write(line, document.RootElement);
            }
            catch (Exception exception)
            {
                Console.Error.WriteLine($"logquill: {transport.GetType().Name} failed to write a record: {exception.Message}");
            }
        }
    }

    private static string? Failed(string message)
    {
        Console.Error.WriteLine($"logquill: {message}");
        return null;
    }
}
