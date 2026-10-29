package io.logquill;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Supplier;

/**
 * Fields bound to every record logged inside a scope, by any {@link Logger}. It is thread-local: other threads do not see it,
 * and a thread started inside the scope does not inherit it. The call's own fields win over the context.
 *
 * <pre>{@code LogContext.with(Map.of("request_id", "r-1"), () -> log.info("handling"));}</pre>
 */
public final class LogContext {
    private static final ThreadLocal<Map<String, Object>> CURRENT = new ThreadLocal<>();

    private LogContext() {}

    /** The fields bound right now (empty outside any scope). */
    public static Map<String, Object> fields() {
        Map<String, Object> current = CURRENT.get();
        return current == null ? Collections.emptyMap() : current;
    }

    /** Runs {@code body} with {@code fields} added to the context. */
    public static void with(Map<String, ?> fields, Runnable body) {
        with(fields, () -> {
            body.run();
            return null;
        });
    }

    /** Runs {@code body} with {@code fields} added to the context and returns its result. */
    public static <T> T with(Map<String, ?> fields, Supplier<T> body) {
        Map<String, Object> previous = CURRENT.get();
        Map<String, Object> merged = new LinkedHashMap<>(previous == null ? Map.of() : previous);
        merged.putAll(fields);
        CURRENT.set(merged);
        try {
            return body.get();
        } finally {
            if (previous == null) {
                CURRENT.remove();
            } else {
                CURRENT.set(previous);
            }
        }
    }
}
