package examples;

import io.logquill.Core;
import io.logquill.Level;
import io.logquill.Logger;
import io.logquill.LoggerOptions;
import io.logquill.Plugins;
import java.util.Map;

/**
 * Per-call cost of the JNA layer: a log call with redaction and PII scanning, a call the level filters out, and a stateless
 * helper. Run with {@code gradle bench} after {@code cargo build -p logquill-ffi --release}. The numbers are for this machine
 * and one JVM run; they say how much one call costs, not how a service behaves under load.
 */
public class Bench {
    private static final int WARMUP = 50_000;
    private static final int RUNS = 200_000;

    private static double nanosPerCall(Runnable call) {
        for (int i = 0; i < WARMUP; i++) {
            call.run();
        }
        long start = System.nanoTime();
        for (int i = 0; i < RUNS; i++) {
            call.run();
        }
        return (System.nanoTime() - start) / (double) RUNS;
    }

    public static void main(String[] args) {
        Map<String, Object> meta = Map.of("user", "alice@example.com", "password", "hunter2", "ms", 12);
        try (Logger redacting = new Logger(LoggerOptions.builder().plugin(Plugins.redact()).plugin(Plugins.pii()).build());
                Logger filtering = new Logger(LoggerOptions.builder().level(Level.ERROR).build())) {
            System.out.printf("log call, redact + pii, returning the record   %8.0f ns%n",
                    nanosPerCall(() -> redacting.logRecord(Level.INFO, "signed in", meta, null)));
            System.out.printf("log call below the level                        %8.0f ns%n",
                    nanosPerCall(() -> filtering.info("below", meta)));
            System.out.printf("helper: canonicalJson                           %8.0f ns%n",
                    nanosPerCall(() -> Core.canonicalJson("{\"b\":1,\"a\":[1,2,3]}")));
        }
    }
}
