package io.logquill;

import java.util.Map;

/**
 * A transport written in Java: called on the logging thread with the finished, already-redacted record, after the Rust
 * transports have been handed it. Keep {@link #write} quick, or queue inside it.
 */
public interface HostTransport {
    /** Writes one record, as its JSON line and parsed. */
    void write(String line, Map<String, Object> record);

    /** Sends anything buffered. The default does nothing. */
    default void flush() {}

    /** Called once from {@link Logger#close}. The default does nothing. */
    default void close() {}
}
