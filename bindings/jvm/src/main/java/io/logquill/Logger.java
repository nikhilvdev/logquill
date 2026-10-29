package io.logquill;

import com.fasterxml.jackson.core.type.TypeReference;
import com.sun.jna.Pointer;
import com.sun.jna.ptr.PointerByReference;
import java.lang.ref.Cleaner;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * A logger on the Rust core: the level filter, redaction and the hash chain run in Rust before anything is written.
 *
 * <pre>{@code
 * try (Logger log = new Logger(LoggerOptions.builder()
 *         .plugin(Plugins.redact()).plugin(Plugins.pii())
 *         .transport(Transports.file("web.log")).build())) {
 *     log.info("signed in", Map.of("user", "alice@example.com", "password", "hunter2"));
 * }
 * }</pre>
 *
 * The constructor throws {@link LogQuillException.Config} for a config the core rejects; a log call never throws. Safe to use
 * from many threads. Close it, or let it be garbage-collected: the native logger is freed either way, but only
 * {@link #close} flushes the background queue.
 */
public final class Logger implements AutoCloseable {
    private static final Cleaner CLEANER = Cleaner.create();
    private static final TypeReference<Map<String, Object>> MAP = new TypeReference<>() {};

    private record Release(Pointer handle, AtomicBoolean done) implements Runnable {
        @Override
        public void run() {
            if (done.compareAndSet(false, true)) {
                Native.LIB.lq_logger_free(handle);
            }
        }
    }

    private final Pointer handle;
    private final Cleaner.Cleanable cleanable;
    private final List<HostTransport> host;
    private final String name;

    /** Builds a logger from {@code options}. */
    public Logger(LoggerOptions options) {
        Map<String, Object> config = new LinkedHashMap<>();
        config.put("name", options.name);
        config.put("level", options.level.wire());
        config.put("plugins", options.plugins);
        config.put("transports", options.transports);
        config.put("tamper_evident", options.tamperEvident);
        if (options.contentPolicy != null) {
            config.put("content_policy", options.contentPolicy);
        }
        if (options.queue != null) {
            config.put("queue", options.queue);
        }
        PointerByReference error = new PointerByReference();
        Pointer pointer = Native.LIB.lq_logger_new(Json.dump(config), error);
        if (pointer == null) {
            throw new LogQuillException.Config(Native.errorText(error, "the logger could not be created"));
        }
        this.handle = pointer;
        this.cleanable = CLEANER.register(this, new Release(pointer, new AtomicBoolean()));
        this.host = options.hostTransports;
        this.name = options.name;
    }

    /** A logger with the defaults: name {@code app}, level INFO, no transports (records are only returned). */
    public Logger() {
        this(LoggerOptions.builder().build());
    }

    private Logger(Pointer pointer, List<HostTransport> host, String name) {
        this.handle = pointer;
        this.cleanable = CLEANER.register(this, new Release(pointer, new AtomicBoolean()));
        this.host = host;
        this.name = name;
    }

    /** This logger's name ({@code web}, {@code web.db}, ...). */
    public String name() {
        return name;
    }

    /** A logger named {@code <name>.<suffix>} with {@code fixedMeta} on every record, sharing this logger's level and transports. */
    public Logger child(String suffix, Object fixedMeta) {
        PointerByReference error = new PointerByReference();
        Pointer pointer = Native.LIB.lq_logger_child(handle, Json.clean(suffix), Json.dumpObject(fixedMeta), error);
        if (pointer == null) {
            throw new LogQuillException(Native.errorText(error));
        }
        return new Logger(pointer, host, name + "." + suffix);
    }

    /** A child with no fixed meta. */
    public Logger child(String suffix) {
        return child(suffix, null);
    }

    /** Logs at TRACE. Returns true when the record was kept, false when it was filtered. Like every log method it never throws. */
    public boolean trace(String message, Object meta) {
        return log(Level.TRACE, message, meta, null);
    }

    /** Logs at DEBUG. */
    public boolean debug(String message, Object meta) {
        return log(Level.DEBUG, message, meta, null);
    }

    /** Logs at INFO. */
    public boolean info(String message, Object meta) {
        return log(Level.INFO, message, meta, null);
    }

    /** Logs at WARN. */
    public boolean warn(String message, Object meta) {
        return log(Level.WARN, message, meta, null);
    }

    /** Logs at ERROR. */
    public boolean error(String message, Object meta) {
        return log(Level.ERROR, message, meta, null);
    }

    /** Logs at FATAL. */
    public boolean fatal(String message, Object meta) {
        return log(Level.FATAL, message, meta, null);
    }

    /** Logs at TRACE, with no meta. */
    public boolean trace(String message) {
        return trace(message, null);
    }

    /** Logs at DEBUG, with no meta. */
    public boolean debug(String message) {
        return debug(message, null);
    }

    /** Logs at INFO, with no meta. */
    public boolean info(String message) {
        return info(message, null);
    }

    /** Logs at WARN, with no meta. */
    public boolean warn(String message) {
        return warn(message, null);
    }

    /** Logs at ERROR, with no meta. */
    public boolean error(String message) {
        return error(message, null);
    }

    /** Logs at FATAL, with no meta. */
    public boolean fatal(String message) {
        return fatal(message, null);
    }

    /**
     * The general call. {@code meta} is a map or any bean (properties become snake_case keys); {@code llm} is an LLM-call block
     * ({@code model}, {@code tokens_in}, ...). A value JSON cannot carry (NaN, a cycle, a getter that throws) is turned into text.
     */
    public boolean log(Level level, String message, Object meta, Object llm) {
        return logRecord(level, message, meta, llm) != null;
    }

    /** Like {@link #log}, returning the finished record as JSON text, or null when it was filtered. */
    public String logRecord(Level level, String message, Object meta, Object llm) {
        try {
            PointerByReference record = new PointerByReference();
            PointerByReference error = new PointerByReference();
            int code = Native.LIB.lq_log(handle, level.wire(), null, Json.clean(message), mergedMeta(meta),
                    llm == null ? null : Json.dumpObject(llm), record, error);
            if (code < 0) {
                return failed(Native.errorText(error));
            }
            Pointer pointer = record.getValue();
            if (code == 0 || pointer == null) {
                return null;
            }
            String line = Native.take(pointer);
            deliver(line);
            return line;
        } catch (Throwable e) {
            return failed(String.valueOf(e.getMessage()));
        }
    }

    private static String mergedMeta(Object meta) {
        Map<String, Object> context = LogContext.fields();
        if (context.isEmpty()) {
            return Json.dumpObject(meta);
        }
        Map<String, Object> merged = new LinkedHashMap<>(context);
        String own = Json.dumpObject(meta);
        if (own != null) {
            try {
                merged.putAll(Json.MAPPER.readValue(own, MAP));
            } catch (Exception e) {
                merged.put("meta", own);
            }
        }
        return Json.dump(merged);
    }

    private void deliver(String line) {
        if (host.isEmpty()) {
            return;
        }
        Map<String, Object> parsed;
        try {
            parsed = Json.MAPPER.readValue(line, MAP);
        } catch (Exception e) {
            parsed = Map.of();
        }
        for (HostTransport transport : host) {
            try {
                transport.write(line, parsed);
            } catch (Throwable e) {
                System.err.println("logquill: " + transport.getClass().getSimpleName() + " failed to write a record: " + e.getMessage());
            }
        }
    }

    private static String failed(String message) {
        System.err.println("logquill: " + message);
        return null;
    }

    /** Changes the level for this logger, its parent and its children. */
    public void setLevel(Level level) {
        PointerByReference error = new PointerByReference();
        if (Native.LIB.lq_logger_set_level(handle, level.wire(), error) < 0) {
            throw new IllegalArgumentException(Native.errorText(error));
        }
    }

    /** Waits up to {@code timeoutMillis} for queued records to be written and flushes Java transports. True when done in time. */
    public boolean flush(int timeoutMillis) {
        host.forEach(HostTransport::flush);
        return Native.LIB.lq_logger_flush(handle, timeoutMillis) == 1;
    }

    /** {@link #flush(int)} with a 5 second timeout. */
    public boolean flush() {
        return flush(5000);
    }

    /** Flushes, stops the background thread and closes Java transports. Call once, on shutdown. */
    @Override
    public void close() {
        Native.LIB.lq_logger_close(handle, 5000);
        host.forEach(HostTransport::close);
        cleanable.clean();
    }
}
