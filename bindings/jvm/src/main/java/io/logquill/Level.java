package io.logquill;

/** Log levels, in order of severity. */
public enum Level {
    /** Finest detail. */
    TRACE,
    /** Diagnostic detail. */
    DEBUG,
    /** Normal operation. */
    INFO,
    /** Something unexpected that was handled. */
    WARN,
    /** An operation failed. */
    ERROR,
    /** The application cannot continue. */
    FATAL;

    String wire() {
        return name().toLowerCase(java.util.Locale.ROOT);
    }
}
