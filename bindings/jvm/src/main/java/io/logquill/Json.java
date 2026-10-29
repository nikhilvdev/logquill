package io.logquill;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.json.JsonMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Turns any Java value into JSON text without throwing: logging must never crash the application over what it was asked to
 * log. Objects serialize with snake_case property names (the record contract's spelling); NaN and the infinities become the
 * strings "NaN", "Infinity" and "-Infinity"; dates become ISO 8601; a cycle, a getter that throws or a type Jackson cannot
 * handle is replaced by its {@code toString()}, or {@code <unprintable T>} if that throws.
 */
final class Json {
    static final ObjectMapper MAPPER = JsonMapper.builder()
            .addModule(new JavaTimeModule())
            .propertyNamingStrategy(PropertyNamingStrategies.SNAKE_CASE)
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .disable(SerializationFeature.FAIL_ON_EMPTY_BEANS)
            .build();

    private Json() {}

    /** JSON text for {@code value}; never throws. */
    static String dump(Object value) {
        try {
            return MAPPER.writeValueAsString(value);
        } catch (Throwable e) { // includes StackOverflowError from a cycle
            return quote(printable(value));
        }
    }

    /** {@code meta} as a JSON object, or null when there is none or it is empty. An entry that cannot be serialized becomes its text. */
    static String dumpObject(Object meta) {
        if (meta == null) {
            return null;
        }
        try {
            JsonNode node = MAPPER.valueToTree(meta);
            if (node.isObject()) {
                return node.isEmpty() ? null : MAPPER.writeValueAsString(node);
            }
            return dump(Map.of("value", meta));
        } catch (Throwable e) {
            Map<String, Object> salvaged = new LinkedHashMap<>();
            if (meta instanceof Map<?, ?> map) {
                for (Map.Entry<?, ?> entry : map.entrySet()) {
                    salvaged.put(printable(entry.getKey()), salvage(entry.getValue()));
                }
            } else {
                salvaged.put("value", printable(meta));
            }
            return dump(salvaged);
        }
    }

    private static Object salvage(Object value) {
        try {
            MAPPER.writeValueAsString(value);
            return value;
        } catch (Throwable e) {
            return printable(value);
        }
    }

    static String printable(Object value) {
        try {
            return String.valueOf(value);
        } catch (Throwable e) {
            return "<unprintable " + (value == null ? "null" : value.getClass().getSimpleName()) + ">";
        }
    }

    private static String quote(String text) {
        try {
            return MAPPER.writeValueAsString(text);
        } catch (Throwable e) {
            return "\"<unserializable>\"";
        }
    }

    /** Replaces NUL (which a C string cannot carry) with U+2400. */
    static String clean(String text) {
        return text == null || text.indexOf('\0') < 0 ? text : text.replace('\0', '␀');
    }
}
