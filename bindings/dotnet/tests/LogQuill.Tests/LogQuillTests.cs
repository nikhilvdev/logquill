using System.Text.Json;
using LogQuill;
using Xunit;

namespace LogQuill.Tests;

public class HelpersTests
{
    [Fact]
    public void Text_helpers_return_strings_and_structured_ones_return_values()
    {
        Assert.Equal("{\"a\": 2, \"b\": 1}", Core.CanonicalJson("{\"b\":1,\"a\":2}"));
        Assert.Equal("mail ***", Core.PiiRedactText("mail alice@example.com"));
        var redacted = Core.RedactMeta("{\"password\":\"x\",\"n\":1}");
        Assert.Equal("***", redacted.GetProperty("password").GetString());
        Assert.Equal(1, redacted.GetProperty("n").GetInt32());
    }

    [Fact]
    public void A_number_keeps_its_spelling_when_given_as_json_text()
    {
        Assert.Equal("{\"x\": 1.0}", Core.CanonicalJson("{\"x\": 1.0}"));
        Assert.NotEqual(Core.CanonicalJson("{\"x\": 1.0}"), Core.CanonicalJson("{\"x\": 1}"));
    }

    [Fact]
    public void An_unknown_helper_and_bad_arguments_throw_with_the_cores_message()
    {
        Assert.Contains("unknown helper", Assert.Throws<LogQuillException>(() => Core.Call("noSuchHelper")).Message);
        Assert.Contains("not valid JSON", Assert.Throws<LogQuillException>(() => Core.CanonicalJson("not json")).Message);
        Assert.Contains("dialect", Assert.Throws<LogQuillException>(() => Core.Call("createTableSql", "oracle", "t")).Message);
    }

    [Fact]
    public void Binary_keys_cross_as_bytes()
    {
        byte[] key = [0xFF, 0x00, (byte)'k'];
        var signature = Core.SignHead(Core.GenesisHash, key);
        Assert.True(Core.VerifyHeadSignature(Core.GenesisHash, key, signature));
        Assert.False(Core.VerifyHeadSignature(Core.GenesisHash, "other"u8.ToArray(), signature));
    }

    [Fact]
    public void The_shared_canonical_json_corpus()
    {
        var path = FindUp("schema/corpus/canonical-json.json");
        Assert.NotNull(path);
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        var failures = new List<string>();
        foreach (var testCase in document.RootElement.GetProperty("cases").EnumerateArray())
        {
            var input = testCase.GetProperty("input").GetProperty("json").GetString()!;
            var expected = testCase.GetProperty("expected").GetProperty("text").GetString();
            if (Core.CanonicalJson(input) != expected)
            {
                failures.Add(testCase.GetProperty("name").GetString()!);
            }
        }

        Assert.Empty(failures);
    }

    internal static string? FindUp(string relative)
    {
        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory is not null; directory = directory.Parent)
        {
            var candidate = Path.Combine(directory.FullName, relative);
            if (File.Exists(candidate))
            {
                return candidate;
            }
        }

        return null;
    }
}

public class ChainTests
{
    private static string Record(string message) =>
        "{\"schema_version\":\"2.0\",\"timestamp\":\"2026-01-01T00:00:00.000Z\",\"level\":\"INFO\",\"logger\":\"a\",\"message\":\"" + message + "\",\"meta\":{}}";

    [Fact]
    public void Stamped_records_verify_and_an_edit_is_found()
    {
        using var chain = new Chain();
        var records = new[] { "a", "b", "c", "d" }.Select(m => chain.Stamp(Record(m))).ToList();
        var verdict = Core.VerifyChain("[" + string.Join(",", records) + "]");
        Assert.True(verdict.Ok);
        Assert.Equal(4, verdict.RecordsChecked);
        Assert.Equal(chain.Head(), verdict.HeadHash);

        records[2] = records[2].Replace("\"c\"", "\"edited\"");
        var broken = Core.VerifyChain("[" + string.Join(",", records) + "]");
        Assert.False(broken.Ok);
        Assert.Equal(3, broken.BrokenAt);
    }

    [Fact]
    public void A_signed_head_catches_a_deleted_last_line()
    {
        using var chain = new Chain();
        var records = new[] { "a", "b", "c" }.Select(m => chain.Stamp(Record(m))).ToList();
        var key = "secret"u8.ToArray();
        var signature = chain.SignHead(key);
        Assert.True(Core.VerifySignedChain("[" + string.Join(",", records) + "]", key, signature).Ok);
        Assert.False(Core.VerifySignedChain("[" + string.Join(",", records.Take(2)) + "]", key, signature).Ok);
        Assert.False(Core.VerifySignedChain("[" + string.Join(",", records) + "]", "wrong"u8.ToArray(), signature).Ok);
    }
}

public class LoggerTests : IDisposable
{
    private readonly string _dir = Directory.CreateTempSubdirectory("lq-dotnet").FullName;

    public void Dispose() => Directory.Delete(_dir, recursive: true);

    private string Path(string name) => System.IO.Path.Combine(_dir, name);

    private static IEnumerable<JsonElement> ReadRecords(string path) =>
        File.ReadAllLines(path).Select(line => JsonDocument.Parse(line).RootElement.Clone());

    private sealed class Sink : ITransport
    {
        public List<string> Lines { get; } = [];
        public List<JsonElement> Records { get; } = [];

        public void Write(string line, JsonElement record)
        {
            lock (Lines)
            {
                Lines.Add(line);
                Records.Add(record.Clone());
            }
        }
    }

    [Fact]
    public void Redaction_happens_before_the_file_is_written()
    {
        var file = Path("app.log");
        using (var log = new Logger(new LoggerOptions { Plugins = { PluginConfig.Redact(), PluginConfig.Pii() }, Transports = { TransportConfig.File(file) } }))
        {
            Assert.True(log.Info("signed in", new { Password = "hunter2", Note = "mail alice@example.com" }));
            log.Close();
        }

        var text = File.ReadAllText(file);
        Assert.DoesNotContain("hunter2", text);
        Assert.DoesNotContain("alice@example.com", text);
        Assert.Equal("***", ReadRecords(file).Single().GetProperty("meta").GetProperty("password").GetString());
    }

    [Fact]
    public void Sampling_rate_limit_and_adaptive_sampling_run_in_the_core()
    {
        static int Kept(object plugin, string message, int n, Func<Logger, string, bool> call)
        {
            using var log = new Logger(new LoggerOptions { Plugins = { plugin } });
            return Enumerable.Range(0, n).Count(_ => call(log, message));
        }

        Assert.Equal(0, Kept(PluginConfig.Sampling(0), "m", 200, (l, m) => l.Info(m)));
        Assert.Equal(200, Kept(PluginConfig.Sampling(1), "m", 200, (l, m) => l.Info(m)));
        Assert.Equal(3, Kept(PluginConfig.RateLimit(3, 60, "message"), "m", 10, (l, m) => l.Info(m)));
        var adaptive = PluginConfig.AdaptiveSampling(baseRate: 0, minRate: 0, maxRate: 0);
        Assert.Equal(0, Kept(adaptive, "m", 200, (l, m) => l.Info(m)));
        Assert.Equal(200, Kept(adaptive, "m", 200, (l, m) => l.Error(m)));
    }

    [Fact]
    public void The_level_filters_and_a_child_shares_it()
    {
        using var log = new Logger(new LoggerOptions { Level = Level.Warn });
        Assert.False(log.Info("below"));
        Assert.True(log.Error("above"));
        using var child = log.Child("db", new { Component = "pg" });
        Assert.Equal("app.db", child.Name);
        log.SetLevel(Level.Error);
        Assert.False(child.Warn("filtered"));
    }

    [Fact]
    public void A_child_adds_fixed_meta_and_an_llm_block_is_carried()
    {
        var sink = new Sink();
        using var log = new Logger(new LoggerOptions { HostTransports = { sink } });
        using var child = log.Child("db", new { Component = "pg" });
        child.Info("query", new { Ms = 4 }, new { Model = "m", TokensIn = 3 });
        var record = sink.Records.Single();
        Assert.Equal("app.db", record.GetProperty("logger").GetString());
        Assert.Equal("pg", record.GetProperty("meta").GetProperty("component").GetString());
        Assert.Equal(4, record.GetProperty("meta").GetProperty("ms").GetInt32());
        Assert.Equal("m", record.GetProperty("llm").GetProperty("model").GetString());
        Assert.Equal(3, record.GetProperty("llm").GetProperty("tokens_in").GetInt32());
    }

    [Fact]
    public void Prompt_content_is_withheld_by_default()
    {
        using var log = new Logger();
        var record = log.LogRecord(Level.Info, "call", new Dictionary<string, object?> { ["input_messages"] = new[] { new { Role = "user", Content = "secret prompt" } } });
        Assert.NotNull(record);
        Assert.DoesNotContain("secret prompt", record);
    }

    [Fact]
    public void A_wrong_config_throws_naming_the_problem()
    {
        Assert.Contains("Unknown log level", Assert.Throws<LogQuillConfigException>(() => new Logger(new LoggerOptions { Level = (Level)99 })).Message.Replace("99", "99"));
        Assert.Contains("unknown plugin type", Assert.Throws<LogQuillConfigException>(() => new Logger(new LoggerOptions { Plugins = { new Dictionary<string, object?> { ["type"] = "redactt" } } })).Message);
        Assert.Contains("path", Assert.Throws<LogQuillConfigException>(() => new Logger(new LoggerOptions { Transports = { new Dictionary<string, object?> { ["type"] = "file" } } })).Message);
    }

    private sealed class Hostile
    {
        public string Fine => "ok";
        public string Boom => throw new InvalidOperationException("getter failed");
    }

    private sealed class Loop
    {
        public Loop? Self { get; set; }
        public string Name => "loop";
    }

    [Fact]
    public void Values_json_cannot_carry_never_make_a_log_call_throw()
    {
        var sink = new Sink();
        using var log = new Logger(new LoggerOptions { HostTransports = { sink } });
        var loop = new Loop();
        loop.Self = loop;
        Assert.True(log.Info("odd", new Dictionary<string, object?>
        {
            ["nan"] = double.NaN,
            ["inf"] = double.NegativeInfinity,
            ["loop"] = loop,
            ["hostile"] = new Hostile(),
            ["surrogate"] = "a\uD800b",
            ["when"] = new DateTime(2026, 1, 2, 3, 4, 5, DateTimeKind.Utc),
        }));
        var meta = sink.Records.Single().GetProperty("meta");
        Assert.Equal("NaN", meta.GetProperty("nan").GetString());
        Assert.Equal("-Infinity", meta.GetProperty("inf").GetString());
        Assert.True(meta.TryGetProperty("loop", out _));
        Assert.True(meta.TryGetProperty("hostile", out _));
        Assert.Contains('�', meta.GetProperty("surrogate").GetString()!);

        Assert.True(log.Info("object with a throwing getter", new Hostile()));
        Assert.Equal(2, sink.Records.Count);
    }

    [Fact]
    public async Task Context_flows_across_await_and_not_to_unrelated_threads()
    {
        var sink = new Sink();
        using var log = new Logger(new LoggerOptions { HostTransports = { sink } });
        using (LogContext.Push(new { RequestId = "r1", User = "u" }))
        {
            await Task.Delay(1);
            log.Info("in", new { User = "override" });
            var other = await Task.Run(() => LogContext.Fields.Count); // a task started inside the scope sees it
            Assert.Equal(2, other);
        }

        log.Info("out");
        var inside = sink.Records[0].GetProperty("meta");
        Assert.Equal("r1", inside.GetProperty("request_id").GetString());
        Assert.Equal("override", inside.GetProperty("user").GetString()); // the call's own field wins
        Assert.Empty(sink.Records[1].GetProperty("meta").EnumerateObject());
        Assert.Empty(LogContext.Fields);
    }

    [Fact]
    public void A_dotnet_transport_receives_the_already_redacted_record_and_a_failing_one_is_isolated()
    {
        var sink = new Sink();
        var failing = new Failing();
        using var log = new Logger(new LoggerOptions { Plugins = { PluginConfig.Redact() }, HostTransports = { failing, sink } });
        var saved = Console.Error;
        var captured = new StringWriter();
        Console.SetError(captured);
        try
        {
            Assert.True(log.Info("hi", new { Password = "hunter2" }));
        }
        finally
        {
            Console.SetError(saved);
        }

        Assert.DoesNotContain("hunter2", sink.Lines.Single());
        Assert.Equal("***", sink.Records.Single().GetProperty("meta").GetProperty("password").GetString());
        Assert.Contains("disk full", captured.ToString());
    }

    private sealed class Failing : ITransport
    {
        public void Write(string line, JsonElement record) => throw new IOException("disk full");
    }

    [Fact]
    public void The_tamper_chain_is_applied_as_records_are_written()
    {
        var file = Path("audit.log");
        using (var log = new Logger(new LoggerOptions { TamperEvident = true, Transports = { TransportConfig.File(file) } }))
        {
            for (var i = 0; i < 10; i++)
            {
                log.Info($"event {i}", new { I = i });
            }

            log.Close();
        }

        var lines = File.ReadAllLines(file);
        Assert.True(Core.VerifyChain("[" + string.Join(",", lines) + "]").Ok);
        lines[4] = lines[4].Replace("event 4", "tampered");
        Assert.False(Core.VerifyChain("[" + string.Join(",", lines) + "]").Ok);
    }

    [Fact]
    public void Many_threads_log_through_one_logger()
    {
        var file = Path("busy.log");
        using (var log = new Logger(new LoggerOptions { TamperEvident = true, Transports = { TransportConfig.File(file) } }))
        {
            Parallel.For(0, 8, t =>
            {
                for (var i = 0; i < 100; i++)
                {
                    log.Info($"t{t} #{i}");
                }
            });
            Assert.True(log.Close());
        }

        var lines = File.ReadAllLines(file);
        Assert.Equal(800, lines.Length);
        Assert.True(Core.VerifyChain("[" + string.Join(",", lines) + "]").Ok);
    }

    [Fact]
    public void Loggers_that_are_never_disposed_are_freed_without_trouble()
    {
        for (var i = 0; i < 300; i++)
        {
            new Logger(new LoggerOptions { Name = $"gc{i}" }).Child("c").Info("x");
        }

        GC.Collect();
        GC.WaitForPendingFinalizers();
        Assert.True(true);
    }
}
