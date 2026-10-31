package io.logquill;

import com.fasterxml.jackson.databind.JsonNode;
import com.sun.jna.Pointer;
import com.sun.jna.ptr.PointerByReference;
import java.io.IOException;
import java.util.Base64;

/**
 * The core's stateless helpers. {@link #call} runs any of them by the name the Node addon uses ({@code canonicalJson},
 * {@code verifyChain}, {@code piiRedactText}, ...); the typed methods cover the common ones. An argument that is a record, meta
 * or list of records may be an object, or a {@link String} holding its JSON text: <b>pass text when a number's spelling
 * matters</b>, since {@code 1} and {@code 1.0} hash differently in a tamper-evident chain.
 */
public final class Core {
    private Core() {}

    /** What verifying a hash chain found. {@code brokenAt} is the 1-based position of the first record that failed, if any. */
    public record Verification(boolean ok, int recordsChecked, Integer brokenAt, String reason, String headHash) {}

    /** The library's version, e.g. {@code 3.0.0-dev.0}. */
    public static String version() {
        return Native.LIB.lq_version().getString(0, "UTF-8");
    }

    /** Runs the helper {@code name} and returns its result. Throws {@link LogQuillException} with the core's message. */
    public static JsonNode call(String name, Object... args) {
        StringBuilder wire = new StringBuilder("[");
        for (int i = 0; i < args.length; i++) {
            if (i > 0) {
                wire.append(',');
            }
            Object arg = args[i];
            wire.append(arg instanceof byte[] key
                    ? Json.dump(Base64.getEncoder().encodeToString(key)) // binary keys cross as base64
                    : Json.dump(arg));
        }
        wire.append(']');
        PointerByReference error = new PointerByReference();
        Pointer result = Native.LIB.lq_call(Json.clean(name), wire.toString(), error);
        if (result == null) {
            throw new LogQuillException(Native.errorText(error));
        }
        try {
            return Json.MAPPER.readTree(Native.take(result));
        } catch (IOException e) {
            throw new LogQuillException("the core returned something that is not JSON: " + e.getMessage());
        }
    }

    /** The canonical text of a JSON document: what the tamper chain hashes. */
    public static String canonicalJson(String json) {
        return call("canonicalJson", json).asText();
    }

    /** {@code text} with emails, card numbers, phone numbers, SSNs and API keys replaced ({@code "***"} unless {@code replacement} is given). */
    public static String piiRedactText(String text, String replacement) {
        return call("piiRedactText", text, replacement).asText();
    }

    /** A copy of {@code meta} (an object or its JSON text) with the values of secret-named keys replaced. */
    public static JsonNode redactMeta(Object meta) {
        return call("redactMeta", meta);
    }

    /** Verifies a hash-chained list of records (JSON text of an array, or any list of records). {@code genesis} null is the standard one. */
    public static Verification verifyChain(Object records, String genesis) {
        return verification(call("verifyChain", records, genesis));
    }

    /** Verifies a chain and that its head matches {@code signature}; catches a deleted last line. */
    public static Verification verifySignedChain(Object records, byte[] key, String signature, String genesis) {
        return verification(call("verifySignedChain", records, key, signature, genesis));
    }

    /** HMAC-SHA256 of a chain head under {@code key}, hex-encoded. */
    public static String signHead(String headHash, byte[] key) {
        return call("signHead", headHash, key).asText();
    }

    /** Whether {@code signature} is what {@link #signHead} gives (compared in constant time). */
    public static boolean verifyHeadSignature(String headHash, byte[] key, String signature) {
        return call("verifyHeadSignature", headHash, key, signature).asBoolean();
    }

    /** The hash a chain starts from. */
    public static String genesisHash() {
        return call("genesisHash").asText();
    }

    private static Verification verification(JsonNode node) {
        return new Verification(
                node.get("ok").asBoolean(),
                node.get("recordsChecked").asInt(),
                node.has("brokenAt") ? node.get("brokenAt").asInt() : null,
                node.has("reason") ? node.get("reason").asText() : null,
                node.has("headHash") ? node.get("headHash").asText() : null);
    }
}
