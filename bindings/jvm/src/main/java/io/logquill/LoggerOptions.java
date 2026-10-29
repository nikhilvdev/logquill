package io.logquill;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** What a {@link Logger} is built from. Every option has a default; build one with {@link #builder()}. */
public final class LoggerOptions {
    final String name;
    final Level level;
    final List<Map<String, Object>> plugins;
    final List<Map<String, Object>> transports;
    final List<HostTransport> hostTransports;
    final String contentPolicy;
    final boolean tamperEvident;
    final Map<String, Object> queue;

    private LoggerOptions(Builder b) {
        this.name = b.name;
        this.level = b.level;
        this.plugins = List.copyOf(b.plugins);
        this.transports = List.copyOf(b.transports);
        this.hostTransports = List.copyOf(b.hostTransports);
        this.contentPolicy = b.contentPolicy;
        this.tamperEvident = b.tamperEvident;
        this.queue = b.queue;
    }

    /** A builder with the defaults: name {@code app}, level INFO, no plugins, no transports. */
    public static Builder builder() {
        return new Builder();
    }

    /** Builds {@link LoggerOptions}. */
    public static final class Builder {
        private String name = "app";
        private Level level = Level.INFO;
        private final List<Map<String, Object>> plugins = new ArrayList<>();
        private final List<Map<String, Object>> transports = new ArrayList<>();
        private final List<HostTransport> hostTransports = new ArrayList<>();
        private String contentPolicy;
        private boolean tamperEvident;
        private Map<String, Object> queue;

        private Builder() {}

        /** The logger's name (default {@code app}). */
        public Builder name(String name) {
            this.name = name;
            return this;
        }

        /** The lowest level that is kept (default INFO). */
        public Builder level(Level level) {
            this.level = level;
            return this;
        }

        /** Adds a plugin that runs in Rust, in order: {@link Plugins#redact()}, {@link Plugins#pii()}. */
        public Builder plugin(Map<String, Object> plugin) {
            this.plugins.add(plugin);
            return this;
        }

        /** Adds a Rust transport, written on a background thread: {@link Transports#console}, {@link Transports#file}. */
        public Builder transport(Map<String, Object> transport) {
            this.transports.add(transport);
            return this;
        }

        /** Adds a Java transport, called on the logging thread with the finished record. */
        public Builder hostTransport(HostTransport transport) {
            this.hostTransports.add(transport);
            return this;
        }

        /** {@code off} (default: prompt, completion and tool content is withheld), {@code hash}, {@code truncate} or {@code full}. */
        public Builder contentPolicy(String policy) {
            this.contentPolicy = policy;
            return this;
        }

        /** Chains each record as it is written, so an edited or removed line can be found ({@link Core#verifyChain}). */
        public Builder tamperEvident(boolean on) {
            this.tamperEvident = on;
            return this;
        }

        /** The queue in front of the Rust transports: at most {@code maxSize} records, {@code drop_oldest}, {@code drop_newest} or {@code block} when full. */
        public Builder queue(int maxSize, String backpressure) {
            Map<String, Object> q = new LinkedHashMap<>();
            q.put("max_size", maxSize);
            q.put("backpressure", backpressure);
            this.queue = q;
            return this;
        }

        /** Builds the options. */
        public LoggerOptions build() {
            return new LoggerOptions(this);
        }
    }
}
