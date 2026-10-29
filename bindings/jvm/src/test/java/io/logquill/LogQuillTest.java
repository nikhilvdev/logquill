package io.logquill;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

class LogQuillTest {
    private static final ObjectMapper JSON = new ObjectMapper();

    // ---- helpers -------------------------------------------------------------------------------------------------

    @Test
    void textHelpersReturnStringsAndKeepNumberSpelling() {
        assertEquals("{\"a\": 2, \"b\": 1}", Core.canonicalJson("{\"b\":1,\"a\":2}"));
        assertNotEquals(Core.canonicalJson("{\"x\": 1.0}"), Core.canonicalJson("{\"x\": 1}"));
        assertEquals("mail ***", Core.piiRedactText("mail alice@example.com", null));
        assertEquals("***", Core.redactMeta(Map.of("password", "x")).get("password").asText());
    }

    @Test
    void badCallsThrowWithTheCoresMessage() {
        assertTrue(assertThrows(LogQuillException.class, () -> Core.call("noSuchHelper")).getMessage().contains("unknown helper"));
        assertTrue(assertThrows(LogQuillException.class, () -> Core.canonicalJson("not json")).getMessage().contains("not valid JSON"));
        assertTrue(assertThrows(LogQuillException.class, () -> Core.call("createTableSql", "oracle", "t")).getMessage().contains("dialect"));
    }

    @Test
    void binaryKeysCrossAsBytes() {
        byte[] key = {(byte) 0xFF, 0x00, 'k'};
        String head = Core.genesisHash();
        String signature = Core.signHead(head, key);
        assertTrue(Core.verifyHeadSignature(head, key, signature));
        assertFalse(Core.verifyHeadSignature(head, "other".getBytes(StandardCharsets.UTF_8), signature));
    }

    @Test
    void theSharedCanonicalJsonCorpus() throws IOException {
        File corpus = null;
        for (File dir = new File("").getAbsoluteFile(); dir != null && corpus == null; dir = dir.getParentFile()) {
            File candidate = new File(dir, "schema/corpus/canonical-json.json");
            corpus = candidate.isFile() ? candidate : null;
        }
        assertNotNull(corpus, "schema/corpus/canonical-json.json not found");
        JsonNode cases = JSON.readTree(corpus).get("cases");
        assertTrue(cases.size() > 0);
        for (JsonNode c : cases) {
            assertEquals(c.get("expected").get("text").asText(), Core.canonicalJson(c.get("input").get("json").asText()), c.get("name").asText());
        }
    }

    // ---- the chain -----------------------------------------------------------------------------------------------

    private static String record(String message) {
        return "{\"schema_version\":\"2.0\",\"timestamp\":\"2026-01-01T00:00:00.000Z\",\"level\":\"INFO\",\"logger\":\"a\",\"message\":\""
                + message + "\",\"meta\":{}}";
    }

    @Test
    void stampedRecordsVerifyAndAnEditIsFound() {
        try (Chain chain = new Chain()) {
            List<String> records = new ArrayList<>();
            for (String m : List.of("a", "b", "c", "d")) {
                records.add(chain.stamp(record(m)));
            }
            Core.Verification verdict = Core.verifyChain("[" + String.join(",", records) + "]", null);
            assertTrue(verdict.ok());
            assertEquals(4, verdict.recordsChecked());
            assertEquals(chain.head(), verdict.headHash());

            records.set(2, records.get(2).replace("\"c\"", "\"edited\""));
            Core.Verification broken = Core.verifyChain("[" + String.join(",", records) + "]", null);
            assertFalse(broken.ok());
            assertEquals(3, broken.brokenAt());
        }
    }

    @Test
    void aSignedHeadCatchesADeletedLastLine() {
        try (Chain chain = new Chain()) {
            List<String> records = new ArrayList<>();
            for (String m : List.of("a", "b", "c")) {
                records.add(chain.stamp(record(m)));
            }
            byte[] key = "secret".getBytes(StandardCharsets.UTF_8);
            String signature = chain.signHead(key);
            assertTrue(Core.verifySignedChain("[" + String.join(",", records) + "]", key, signature, null).ok());
            assertFalse(Core.verifySignedChain("[" + String.join(",", records.subList(0, 2)) + "]", key, signature, null).ok());
            assertFalse(Core.verifySignedChain("[" + String.join(",", records) + "]", "wrong".getBytes(StandardCharsets.UTF_8), signature, null).ok());
        }
    }

    // ---- the logger ----------------------------------------------------------------------------------------------

    static final class Sink implements HostTransport {
        final List<String> lines = Collections.synchronizedList(new ArrayList<>());
        final List<Map<String, Object>> records = Collections.synchronizedList(new ArrayList<>());

        @Override
        public void write(String line, Map<String, Object> record) {
            lines.add(line);
            records.add(record);
        }
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> meta(Map<String, Object> record) {
        return (Map<String, Object>) record.get("meta");
    }

    @Test
    void redactionHappensBeforeTheFileIsWritten(@TempDir Path dir) throws IOException {
        Path file = dir.resolve("app.log");
        try (Logger log = new Logger(LoggerOptions.builder().plugin(Plugins.redact()).plugin(Plugins.pii())
                .transport(Transports.file(file.toString())).build())) {
            assertTrue(log.info("signed in", Map.of("password", "hunter2", "note", "mail alice@example.com")));
        }
        String text = Files.readString(file);
        assertFalse(text.contains("hunter2") || text.contains("alice@example.com"), text);
        assertEquals("***", JSON.readTree(text.lines().findFirst().orElseThrow()).get("meta").get("password").asText());
    }

    @Test
    void theLevelFiltersAndAChildSharesIt() {
        try (Logger log = new Logger(LoggerOptions.builder().level(Level.WARN).build())) {
            assertFalse(log.info("below"));
            assertTrue(log.error("above"));
            Logger child = log.child("db", Map.of("component", "pg"));
            assertEquals("app.db", child.name());
            log.setLevel(Level.ERROR);
            assertFalse(child.warn("filtered"));
        }
    }

    @Test
    void aChildAddsFixedMetaAndAnLlmBlockIsCarried() {
        Sink sink = new Sink();
        try (Logger log = new Logger(LoggerOptions.builder().hostTransport(sink).build())) {
            log.child("db", Map.of("component", "pg")).log(Level.INFO, "query", Map.of("ms", 4), Map.of("model", "m", "tokens_in", 3));
        }
        Map<String, Object> record = sink.records.get(0);
        assertEquals("app.db", record.get("logger"));
        assertEquals("pg", meta(record).get("component"));
        assertEquals(4, meta(record).get("ms"));
        @SuppressWarnings("unchecked")
        Map<String, Object> llm = (Map<String, Object>) record.get("llm");
        assertEquals("m", llm.get("model"));
        assertEquals(3, llm.get("tokens_in"));
    }

    @Test
    void promptContentIsWithheldByDefault() {
        try (Logger log = new Logger()) {
            String line = log.logRecord(Level.INFO, "call", Map.of("input_messages", List.of(Map.of("role", "user", "content", "secret prompt"))), null);
            assertNotNull(line);
            assertFalse(line.contains("secret prompt"), line);
        }
    }

    @Test
    void aWrongConfigThrowsNamingTheProblem() {
        assertTrue(assertThrows(LogQuillException.Config.class, () -> new Logger(LoggerOptions.builder().contentPolicy("loud").build()))
                .getMessage().contains("content policy"));
        assertTrue(assertThrows(LogQuillException.Config.class, () -> new Logger(LoggerOptions.builder().plugin(Map.of("type", "redactt")).build()))
                .getMessage().contains("unknown plugin type"));
    }

    static final class Hostile {
        public String getFine() {
            return "ok";
        }

        public String getBoom() {
            throw new IllegalStateException("getter failed");
        }
    }

    @Test
    void valuesJsonCannotCarryNeverMakeALogCallFail() {
        Sink sink = new Sink();
        try (Logger log = new Logger(LoggerOptions.builder().hostTransport(sink).build())) {
            Map<String, Object> cycle = new LinkedHashMap<>();
            cycle.put("self", cycle);
            Map<String, Object> odd = new LinkedHashMap<>();
            odd.put("nan", Double.NaN);
            odd.put("inf", Double.NEGATIVE_INFINITY);
            odd.put("cycle", cycle);
            odd.put("hostile", new Hostile());
            odd.put("when", java.time.Instant.parse("2026-01-02T03:04:05Z"));
            odd.put("nul", "a\0b");
            assertTrue(log.info("odd", odd));
            assertTrue(log.info("a bean with a throwing getter", new Hostile()));
        }
        Map<String, Object> meta = meta(sink.records.get(0));
        assertEquals("NaN", meta.get("nan"));
        assertEquals("-Infinity", meta.get("inf"));
        assertEquals("2026-01-02T03:04:05Z", meta.get("when"));
        assertTrue(meta.containsKey("cycle") && meta.containsKey("hostile"));
        assertEquals(2, sink.records.size());
    }

    @Test
    void contextIsMergedIntoRecordsAndDoesNotLeak() {
        Sink sink = new Sink();
        try (Logger log = new Logger(LoggerOptions.builder().hostTransport(sink).build())) {
            LogContext.with(Map.of("request_id", "r1", "user", "u"), () -> log.info("in", Map.of("user", "override")));
            log.info("out");
            AtomicReference<Map<String, Object>> other = new AtomicReference<>();
            LogContext.with(Map.of("request_id", "t"), () -> {
                Thread thread = new Thread(() -> other.set(LogContext.fields()));
                thread.start();
                try {
                    thread.join();
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                }
            });
            assertTrue(other.get().isEmpty(), "a new thread must not inherit the context");
        }
        assertEquals("r1", meta(sink.records.get(0)).get("request_id"));
        assertEquals("override", meta(sink.records.get(0)).get("user")); // the call's own field wins
        assertTrue(meta(sink.records.get(1)).isEmpty());
        assertTrue(LogContext.fields().isEmpty());
    }

    @Test
    void aJavaTransportReceivesTheAlreadyRedactedRecordAndAFailingOneIsIsolated() {
        Sink sink = new Sink();
        HostTransport failing = (line, record) -> {
            throw new IllegalStateException("disk full");
        };
        try (Logger log = new Logger(LoggerOptions.builder().plugin(Plugins.redact()).hostTransport(failing).hostTransport(sink).build())) {
            assertTrue(log.info("hi", Map.of("password", "hunter2")));
        }
        assertEquals(1, sink.lines.size());
        assertFalse(sink.lines.get(0).contains("hunter2"));
        assertEquals("***", meta(sink.records.get(0)).get("password"));
    }

    @Test
    void theTamperChainIsAppliedAsRecordsAreWritten(@TempDir Path dir) throws IOException {
        Path file = dir.resolve("audit.log");
        try (Logger log = new Logger(LoggerOptions.builder().tamperEvident(true).transport(Transports.file(file.toString())).build())) {
            for (int i = 0; i < 10; i++) {
                log.info("event " + i, Map.of("i", i));
            }
        }
        List<String> lines = new ArrayList<>(Files.readAllLines(file));
        assertTrue(Core.verifyChain("[" + String.join(",", lines) + "]", null).ok());
        lines.set(4, lines.get(4).replace("event 4", "tampered"));
        assertFalse(Core.verifyChain("[" + String.join(",", lines) + "]", null).ok());
    }

    @Test
    void manyThreadsLogThroughOneLogger(@TempDir Path dir) throws Exception {
        Path file = dir.resolve("busy.log");
        Logger log = new Logger(LoggerOptions.builder().tamperEvident(true).transport(Transports.file(file.toString())).build());
        List<Thread> threads = new ArrayList<>();
        for (int t = 0; t < 8; t++) {
            int id = t;
            Thread thread = new Thread(() -> {
                for (int i = 0; i < 100; i++) {
                    log.info("t" + id + " #" + i);
                }
            });
            threads.add(thread);
            thread.start();
        }
        for (Thread thread : threads) {
            thread.join();
        }
        log.close();
        List<String> lines = Files.readAllLines(file);
        assertEquals(800, lines.size());
        assertTrue(Core.verifyChain("[" + String.join(",", lines) + "]", null).ok());
    }

    @Test
    void loggersThatAreNeverClosedAreFreedWithoutTrouble() {
        for (int i = 0; i < 300; i++) {
            new Logger(LoggerOptions.builder().name("gc" + i).build()).child("c").info("x");
        }
        System.gc();
        assertTrue(true);
    }
}
