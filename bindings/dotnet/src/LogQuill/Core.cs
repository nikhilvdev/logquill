using System.Text.Json;

namespace LogQuill;

/// <summary>Thrown for a failure of the core: a wrong config, a bad helper argument, an unknown helper.</summary>
public class LogQuillException : Exception
{
    /// <summary>Creates the exception with the core's message.</summary>
    public LogQuillException(string message) : base(message) { }
}

/// <summary>Thrown by <see cref="Logger"/>'s constructor for a config the core rejects; the message names the option and what is allowed.</summary>
public sealed class LogQuillConfigException : LogQuillException
{
    /// <summary>Creates the exception with the core's message.</summary>
    public LogQuillConfigException(string message) : base(message) { }
}

/// <summary>What verifying a hash chain found.</summary>
/// <param name="Ok">Whether every record checked out.</param>
/// <param name="RecordsChecked">How many records were checked.</param>
/// <param name="BrokenAt">The 1-based position of the first record that failed, if any.</param>
/// <param name="Reason">Why it failed, if it did.</param>
/// <param name="HeadHash">The hash of the last record, when the chain was intact.</param>
public sealed record Verification(bool Ok, int RecordsChecked, int? BrokenAt, string? Reason, string? HeadHash);

/// <summary>
/// The core's stateless helpers. <see cref="Call"/> runs any of them by the name the Node addon uses (<c>canonicalJson</c>,
/// <c>verifyChain</c>, <c>piiRedactText</c>, ...); the typed methods cover the common ones. An argument that is a record, meta
/// or list of records may be an object, or a <see cref="string"/> holding its JSON text: <b>pass text when a number's spelling
/// matters</b>, since <c>1</c> and <c>1.0</c> hash differently in a tamper-evident chain.
/// </summary>
public static class Core
{
    /// <summary>The library's version, e.g. <c>3.0.0-dev.0</c>.</summary>
    public static string Version => System.Runtime.InteropServices.Marshal.PtrToStringUTF8(Native.lq_version()) ?? string.Empty;

    /// <summary>Runs the helper <paramref name="name"/> and returns its result. Throws <see cref="LogQuillException"/> with the core's message.</summary>
    public static JsonElement Call(string name, params object?[] args)
    {
        var wire = new string[args.Length];
        for (var i = 0; i < args.Length; i++)
        {
            wire[i] = args[i] switch
            {
                byte[] bytes => Json.Dump(Convert.ToBase64String(bytes)), // binary keys cross as base64
                string text => Json.Dump(text),
                _ => Json.Dump(args[i]),
            };
        }

        var result = Native.lq_call(name, "[" + string.Join(",", wire) + "]", out var error);
        if (result == IntPtr.Zero)
        {
            throw new LogQuillException(Native.ErrorText(error));
        }

        using var document = JsonDocument.Parse(Native.Take(result));
        return document.RootElement.Clone();
    }

    /// <summary>The canonical text of a JSON document: what the tamper chain hashes.</summary>
    public static string CanonicalJson(string json) => Call("canonicalJson", json).GetString()!;

    /// <summary><paramref name="text"/> with emails, card numbers, phone numbers, SSNs and API keys replaced.</summary>
    public static string PiiRedactText(string text, string? replacement = null) =>
        Call("piiRedactText", text, replacement).GetString()!;

    /// <summary>A copy of <paramref name="meta"/> (an object or its JSON text) with the values of secret-named keys replaced.</summary>
    public static JsonElement RedactMeta(object meta, object? options = null) => Call("redactMeta", meta, options);

    /// <summary>Verifies a hash-chained list of records (a JSON array as text, or any list of records).</summary>
    public static Verification VerifyChain(object records, string? genesis = null) => ToVerification(Call("verifyChain", records, genesis));

    /// <summary>Verifies a chain and that its head matches <paramref name="signature"/>; catches a deleted last line.</summary>
    public static Verification VerifySignedChain(object records, byte[] key, string signature, string? genesis = null) =>
        ToVerification(Call("verifySignedChain", records, key, signature, genesis));

    /// <summary>HMAC-SHA256 of a chain head under <paramref name="key"/>, hex-encoded.</summary>
    public static string SignHead(string headHash, byte[] key) => Call("signHead", headHash, key).GetString()!;

    /// <summary>Whether <paramref name="signature"/> is what <see cref="SignHead"/> gives (compared in constant time).</summary>
    public static bool VerifyHeadSignature(string headHash, byte[] key, string signature) =>
        Call("verifyHeadSignature", headHash, key, signature).GetBoolean();

    /// <summary>The hash a chain starts from.</summary>
    public static string GenesisHash => Call("genesisHash").GetString()!;

    private static Verification ToVerification(JsonElement result) => new(
        result.GetProperty("ok").GetBoolean(),
        result.GetProperty("recordsChecked").GetInt32(),
        result.TryGetProperty("brokenAt", out var at) ? at.GetInt32() : null,
        result.TryGetProperty("reason", out var reason) ? reason.GetString() : null,
        result.TryGetProperty("headHash", out var head) ? head.GetString() : null);
}
