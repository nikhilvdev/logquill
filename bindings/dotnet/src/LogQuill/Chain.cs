namespace LogQuill;

/// <summary>
/// A hash chain: each record stamped through it carries <c>meta.prev_hash</c> and <c>meta.hash</c>, so a log can be checked
/// later for an edited or removed line (<see cref="Core.VerifyChain"/>). Safe to share between threads.
/// </summary>
public sealed class Chain : IDisposable
{
    private readonly ChainHandle _handle;

    /// <summary>Starts a chain from <paramref name="genesis"/> (null: the standard genesis hash).</summary>
    public Chain(string? genesis = null)
    {
        var raw = Native.lq_chain_new(genesis, out var error);
        if (raw == IntPtr.Zero)
        {
            throw new LogQuillException(Native.ErrorText(error));
        }

        _handle = new ChainHandle(raw);
    }

    /// <summary>Chains <paramref name="recordJson"/> (a record as JSON text) and returns it with the hashes set, as JSON text.</summary>
    public string Stamp(string recordJson)
    {
        var result = Native.lq_chain_stamp(_handle, recordJson, out var error);
        if (result == IntPtr.Zero)
        {
            throw new LogQuillException(Native.ErrorText(error));
        }

        return Native.Take(result);
    }

    /// <summary>The hash of the last record chained.</summary>
    public string Head()
    {
        var result = Native.lq_chain_head(_handle, out var error);
        if (result == IntPtr.Zero)
        {
            throw new LogQuillException(Native.ErrorText(error));
        }

        return Native.Take(result);
    }

    /// <summary>HMAC-SHA256 of the head under <paramref name="key"/>, hex-encoded.</summary>
    public string SignHead(byte[] key)
    {
        var result = Native.lq_chain_sign_head(_handle, key, (nuint)key.Length, out var error);
        if (result == IntPtr.Zero)
        {
            throw new LogQuillException(Native.ErrorText(error));
        }

        return Native.Take(result);
    }

    /// <summary>Releases the native chain.</summary>
    public void Dispose() => _handle.Dispose();
}
