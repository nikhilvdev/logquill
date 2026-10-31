package io.logquill;

import java.util.LinkedHashMap;
import java.util.Map;

/** Builders for {@link LoggerOptions.Builder#transport}: transports written by Rust on a background thread. */
public final class Transports {
    private Transports() {}

    /** Writes each record as JSON to standard output (ERROR and FATAL to standard error). */
    public static Map<String, Object> console() {
        return console("json", null);
    }

    /** Writes each record to standard output in {@code format} ({@code json}, {@code text} or {@code logfmt}); {@code colour} null picks by terminal. */
    public static Map<String, Object> console(String format, Boolean colour) {
        Map<String, Object> transport = new LinkedHashMap<>();
        transport.put("type", "console");
        transport.put("format", format);
        if (colour != null) {
            transport.put("colour", colour);
        }
        return transport;
    }

    /** Writes each record as JSON to a file, rotating at 10 MiB and keeping 5 files. */
    public static Map<String, Object> file(String path) {
        return file(path, "json", null, null, null);
    }

    /** Writes each record to a file; null options take the defaults. {@code compression} is {@code gz} or {@code zip}. */
    public static Map<String, Object> file(String path, String format, Long maxBytes, Integer backupCount, String compression) {
        Map<String, Object> transport = new LinkedHashMap<>();
        transport.put("type", "file");
        transport.put("path", path);
        transport.put("format", format);
        if (maxBytes != null) {
            transport.put("max_bytes", maxBytes);
        }
        if (backupCount != null) {
            transport.put("backup_count", backupCount);
        }
        if (compression != null) {
            transport.put("compression", compression);
        }
        return transport;
    }
}
