package io.logquill;

import com.sun.jna.Library;
import com.sun.jna.Pointer;
import com.sun.jna.ptr.PointerByReference;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * The C ABI of the Rust core ({@code crates/logquill-ffi}, {@code include/logquill.h}), loaded with JNA. Nothing outside this
 * class touches a pointer: the rest of the library sees {@link String} and throws {@link LogQuillException}.
 */
final class Native {
    /** The C ABI version this library was written against; a native library with a different one is refused. */
    static final int ABI_VERSION = 1;

    interface Lib extends Library {
        Pointer lq_version();

        int lq_abi_version();

        void lq_string_free(Pointer text);

        Pointer lq_call(String function, String argsJson, PointerByReference error);

        Pointer lq_chain_new(String genesis, PointerByReference error);

        Pointer lq_chain_stamp(Pointer chain, String recordJson, PointerByReference error);

        Pointer lq_chain_head(Pointer chain, PointerByReference error);

        Pointer lq_chain_sign_head(Pointer chain, byte[] key, long keyLength, PointerByReference error);

        void lq_chain_free(Pointer chain);

        Pointer lq_logger_new(String configJson, PointerByReference error);

        Pointer lq_logger_child(Pointer parent, String suffix, String fixedMetaJson, PointerByReference error);

        int lq_log(Pointer logger, String level, String loggerName, String message, String metaJson, String llmJson,
                PointerByReference record, PointerByReference error);

        int lq_logger_set_level(Pointer logger, String level, PointerByReference error);

        int lq_logger_flush(Pointer logger, int timeoutMs);

        int lq_logger_close(Pointer logger, int timeoutMs);

        void lq_logger_free(Pointer logger);
    }

    static final Lib LIB = load();

    private Native() {}

    private static String libraryFile() {
        String os = System.getProperty("os.name").toLowerCase();
        if (os.contains("mac")) {
            return "liblogquill_ffi.dylib";
        }
        return os.contains("win") ? "logquill_ffi.dll" : "liblogquill_ffi.so";
    }

    /**
     * Loads the library, first match wins: the {@code LOGQUILL_LIB} environment variable (a path to the file), JNA's own search
     * (a jar's {@code <os>-<arch>/} resource, {@code jna.library.path}, the system path), then a build in this repository's
     * {@code target/} directory (development).
     */
    private static Lib load() {
        System.setProperty("jna.encoding", "UTF-8");
        List<String> tried = new ArrayList<>();
        List<String> candidates = new ArrayList<>();
        String fromEnvironment = System.getenv("LOGQUILL_LIB");
        if (fromEnvironment != null && !fromEnvironment.isEmpty()) {
            candidates.add(fromEnvironment);
        }
        candidates.add("logquill_ffi");
        for (File dir = new File("").getAbsoluteFile(); dir != null; dir = dir.getParentFile()) {
            for (String profile : new String[] {"release", "debug"}) {
                File file = new File(dir, "target" + File.separator + profile + File.separator + libraryFile());
                if (file.isFile()) {
                    candidates.add(file.getPath());
                }
            }
        }
        for (String candidate : candidates) {
            try {
                Lib lib = com.sun.jna.Native.load(candidate, Lib.class);
                if (lib.lq_abi_version() != ABI_VERSION) {
                    throw new LogQuillException("The LogQuill native library speaks C ABI " + lib.lq_abi_version()
                            + ", this library needs " + ABI_VERSION + ".");
                }
                return lib;
            } catch (UnsatisfiedLinkError e) {
                tried.add(candidate);
            }
        }
        throw new UnsatisfiedLinkError("LogQuill: could not load " + libraryFile() + ". Tried: " + String.join(", ", tried)
                + ". Set LOGQUILL_LIB to the library's path, use a jar that carries it, or run "
                + "`cargo build -p logquill-ffi --release` in the repository.");
    }

    /** Reads and frees an owned C string. */
    static String take(Pointer text) {
        try {
            return text.getString(0, StandardCharsets.UTF_8.name());
        } finally {
            LIB.lq_string_free(text);
        }
    }

    /** The message a failed call left in {@code error} (freed), or {@code fallback}. */
    static String errorText(PointerByReference error, String fallback) {
        Pointer pointer = error.getValue();
        return pointer == null ? fallback : take(pointer);
    }

    static String errorText(PointerByReference error) {
        return errorText(error, "the call failed");
    }
}
