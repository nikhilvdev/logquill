package io.logquill;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

/** The README's example is a class that is run here, and the README must contain it word for word. */
class ReadmeTest {
    private static final Path ROOT = Path.of("").toAbsolutePath();

    @Test
    void theReadmeContainsTheQuickstartVerbatim() throws IOException {
        String example = Files.readString(ROOT.resolve("src/test/java/examples/Quickstart.java")).replace("\r\n", "\n").stripTrailing();
        String readme = Files.readString(ROOT.resolve("README.md")).replace("\r\n", "\n");
        assertTrue(readme.contains("```java\n" + example + "\n```"), "README.md does not contain Quickstart.java verbatim");
    }

    @Test
    void theQuickstartRunsAndWritesARedactedLog(@TempDir Path dir) throws IOException {
        Path file = dir.resolve("web.log");
        examples.Quickstart.main(new String[] {file.toString()});
        List<String> lines = Files.readAllLines(file);
        assertEquals(2, lines.size());
        ObjectMapper json = new ObjectMapper();
        JsonNode first = json.readTree(lines.get(0));
        JsonNode second = json.readTree(lines.get(1));
        assertEquals("***", first.get("meta").get("password").asText());
        assertEquals("***", first.get("meta").get("user").asText());
        assertFalse(String.join("\n", lines).contains("hunter2"));
        assertEquals("web.db", second.get("logger").asText());
        assertEquals(840, second.get("meta").get("ms").asInt());
        assertEquals("pg", second.get("meta").get("component").asText());
    }
}
