package io.logquill;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Builders for {@link LoggerOptions.Builder#plugin}: plugins that run in Rust, in order. */
public final class Plugins {
    private Plugins() {}

    /** Masks the values of keys (case-insensitively, at every depth): password, token, secret, api_key and authorization. */
    public static Map<String, Object> redact() {
        return redact(null, null, null);
    }

    /**
     * Masks the values of {@code keys} (null: the defaults); {@code classes} ({@code secret}, {@code pii}, {@code content}) add the
     * well-known keys of those classes; {@code replacement} null means {@code ***}.
     */
    public static Map<String, Object> redact(List<String> keys, List<String> classes, String replacement) {
        Map<String, Object> plugin = new LinkedHashMap<>();
        plugin.put("type", "redact");
        if (keys != null) {
            plugin.put("keys", keys);
        }
        if (classes != null) {
            plugin.put("classes", classes);
        }
        if (replacement != null) {
            plugin.put("replacement", replacement);
        }
        return plugin;
    }

    /** Masks emails, card numbers, phone numbers, SSNs and API keys inside any string of the record's meta. */
    public static Map<String, Object> pii() {
        return pii(null);
    }

    /** Like {@link #pii()} with a different replacement text. */
    public static Map<String, Object> pii(String replacement) {
        Map<String, Object> plugin = new LinkedHashMap<>();
        plugin.put("type", "pii");
        if (replacement != null) {
            plugin.put("replacement", replacement);
        }
        return plugin;
    }

    /** Keeps about {@code rate} (0 to 1) of the records, chosen at random. */
    public static Map<String, Object> sampling(double rate) {
        Map<String, Object> plugin = new LinkedHashMap<>();
        plugin.put("type", "sampling");
        plugin.put("rate", rate);
        return plugin;
    }

    /**
     * Drops records once more than {@code maxRecords} share a key inside {@code perSeconds}. {@code key} is
     * {@code logger_level} (null: the default), {@code logger} or {@code message}.
     */
    public static Map<String, Object> rateLimit(int maxRecords, double perSeconds, String key) {
        Map<String, Object> plugin = new LinkedHashMap<>();
        plugin.put("type", "rate_limit");
        plugin.put("max_records", maxRecords);
        plugin.put("per_seconds", perSeconds);
        if (key != null) {
            plugin.put("key", key);
        }
        return plugin;
    }

    /**
     * Keeps errors and slow spans, samples the rest at a rate that follows demand, and caps the bytes written per window.
     * {@code options} holds any of {@code base_rate}, {@code min_rate}, {@code max_rate}, {@code keep_at}, {@code slow_ms},
     * {@code max_bytes_per_second} and {@code window_seconds}; what is left out keeps the core's default, and a misspelt name is
     * rejected when the logger is built.
     */
    public static Map<String, Object> adaptiveSampling(Map<String, Object> options) {
        Map<String, Object> plugin = new LinkedHashMap<>();
        plugin.put("type", "adaptive_sampling");
        plugin.putAll(options);
        return plugin;
    }
}
