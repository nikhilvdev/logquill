package examples;

import io.logquill.Logger;
import io.logquill.LoggerOptions;
import io.logquill.Plugins;
import io.logquill.Transports;
import java.util.Map;

public class Quickstart {
    public static void main(String[] args) {
        String path = args.length > 0 ? args[0] : "web.log";
        LoggerOptions options = LoggerOptions.builder()
                .name("web")
                .plugin(Plugins.redact())
                .plugin(Plugins.pii())
                .transport(Transports.file(path))
                .build();
        try (Logger log = new Logger(options)) {
            log.info("signed in", Map.of("user", "alice@example.com", "password", "hunter2"));
            log.child("db", Map.of("component", "pg")).warn("slow query", Map.of("ms", 840));
        }
    }
}
