package io.logquill;

/** Thrown for a failure of the core: a wrong config, a bad helper argument, an unknown helper. The message is the core's. */
public class LogQuillException extends RuntimeException {
    private static final long serialVersionUID = 1L;

    /** Creates the exception with the core's message. */
    public LogQuillException(String message) {
        super(message);
    }

    /** Thrown by {@link Logger}'s constructor for a config the core rejects; the message names the option and what is allowed. */
    public static final class Config extends LogQuillException {
        private static final long serialVersionUID = 1L;

        /** Creates the exception with the core's message. */
        public Config(String message) {
            super(message);
        }
    }
}
