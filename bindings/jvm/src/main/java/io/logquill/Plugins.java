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
}
