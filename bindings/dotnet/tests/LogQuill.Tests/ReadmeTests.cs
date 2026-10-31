using System.Diagnostics;
using System.Text.Json;
using Xunit;

namespace LogQuill.Tests;

/// <summary>The README's example is a project that is run here, and the README must contain it word for word.</summary>
public class ReadmeTests
{
    private static string Root => Path.GetDirectoryName(HelpersTests.FindUp("bindings/dotnet/README.md"))!;

    [Fact]
    public void The_readme_contains_the_quickstart_verbatim()
    {
        var example = File.ReadAllText(Path.Combine(Root, "examples/quickstart/Program.cs")).Replace("\r\n", "\n").TrimEnd();
        var readme = File.ReadAllText(Path.Combine(Root, "README.md")).Replace("\r\n", "\n");
        Assert.Contains("```csharp\n" + example + "\n```", readme);
    }

    [Fact]
    public void The_quickstart_runs_and_writes_a_redacted_log()
    {
        var work = Directory.CreateTempSubdirectory("lq-readme").FullName;
        try
        {
            var start = new ProcessStartInfo("dotnet", ["run", "--project", Path.Combine(Root, "examples/quickstart")])
            {
                WorkingDirectory = work,
                RedirectStandardError = true,
                RedirectStandardOutput = true,
            };
            start.Environment["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1";
            using var process = Process.Start(start)!;
            var error = process.StandardError.ReadToEnd();
            process.StandardOutput.ReadToEnd();
            Assert.True(process.WaitForExit(120_000), "the example did not finish");
            Assert.True(process.ExitCode == 0, error);

            var lines = File.ReadAllLines(Path.Combine(work, "web.log")).Select(line => JsonDocument.Parse(line).RootElement).ToList();
            Assert.Equal(2, lines.Count);
            Assert.Equal("***", lines[0].GetProperty("meta").GetProperty("password").GetString());
            Assert.Equal("***", lines[0].GetProperty("meta").GetProperty("user").GetString());
            Assert.Equal("web.db", lines[1].GetProperty("logger").GetString());
            Assert.Equal(840, lines[1].GetProperty("meta").GetProperty("ms").GetInt32());
        }
        finally
        {
            Directory.Delete(work, recursive: true);
        }
    }
}
