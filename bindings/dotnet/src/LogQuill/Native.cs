using System.Reflection;
using System.Runtime.InteropServices;

namespace LogQuill;

/// <summary>
/// The C ABI of the Rust core (<c>crates/logquill-ffi</c>, <c>include/logquill.h</c>). Nothing outside this file touches a
/// pointer: the rest of the assembly sees <see cref="string"/> and throws <see cref="LogQuillException"/>.
/// </summary>
internal static partial class Native
{
    private const string Library = "logquill_ffi";

    /// <summary>The C ABI version this assembly was written against; a library with a different one is refused.</summary>
    internal const uint AbiVersion = 1;

    static Native()
    {
        NativeLibrary.SetDllImportResolver(typeof(Native).Assembly, Resolve);
        var found = lq_abi_version();
        if (found != AbiVersion)
        {
            throw new LogQuillException($"The LogQuill native library speaks C ABI {found}, this assembly needs {AbiVersion}.");
        }
    }

    private static string FileName =>
        RuntimeInformation.IsOSPlatform(OSPlatform.OSX) ? "liblogquill_ffi.dylib"
        : RuntimeInformation.IsOSPlatform(OSPlatform.Windows) ? "logquill_ffi.dll"
        : "liblogquill_ffi.so";

    /// <summary>
    /// Where the library is looked for, first match wins: the <c>LOGQUILL_LIB</c> environment variable (a path to the file),
    /// the runtime's own probing (a NuGet package's <c>runtimes/&lt;rid&gt;/native/</c>, the application directory), then a build
    /// in this repository's <c>target/</c> directory (development).
    /// </summary>
    private static IntPtr Resolve(string name, Assembly assembly, DllImportSearchPath? searchPath)
    {
        if (name != Library)
        {
            return IntPtr.Zero;
        }

        var tried = new List<string>();
        var fromEnvironment = Environment.GetEnvironmentVariable("LOGQUILL_LIB");
        if (!string.IsNullOrEmpty(fromEnvironment))
        {
            if (NativeLibrary.TryLoad(fromEnvironment, out var explicitHandle))
            {
                return explicitHandle;
            }

            tried.Add($"LOGQUILL_LIB={fromEnvironment}");
        }

        if (NativeLibrary.TryLoad(name, assembly, searchPath, out var handle))
        {
            return handle;
        }

        tried.Add($"{FileName} next to the application and in runtimes/<rid>/native/");
        for (var directory = new DirectoryInfo(AppContext.BaseDirectory); directory is not null; directory = directory.Parent)
        {
            foreach (var profile in new[] { "release", "debug" })
            {
                var candidate = Path.Combine(directory.FullName, "target", profile, FileName);
                if (File.Exists(candidate) && NativeLibrary.TryLoad(candidate, out handle))
                {
                    return handle;
                }
            }
        }

        throw new DllNotFoundException(
            $"LogQuill: could not load {FileName}. Looked at: {string.Join("; ", tried)}. " +
            "Set LOGQUILL_LIB to the library's path, install the package build for this platform, or run " +
            "`cargo build -p logquill-ffi --release` in the repository.");
    }

    // ---- information and memory ---------------------------------------------------------------------------------
    [LibraryImport(Library)] internal static partial uint lq_abi_version();
    [LibraryImport(Library)] internal static partial IntPtr lq_version();
    [LibraryImport(Library)] internal static partial void lq_string_free(IntPtr text);

    // ---- stateless helpers ---------------------------------------------------------------------------------------
    [LibraryImport(Library, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial IntPtr lq_call(string function, string? argsJson, out IntPtr error);

    // ---- hash chain -----------------------------------------------------------------------------------------------
    [LibraryImport(Library, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial IntPtr lq_chain_new(string? genesis, out IntPtr error);

    [LibraryImport(Library, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial IntPtr lq_chain_stamp(ChainHandle chain, string recordJson, out IntPtr error);

    [LibraryImport(Library)]
    internal static partial IntPtr lq_chain_head(ChainHandle chain, out IntPtr error);

    [LibraryImport(Library)]
    internal static partial IntPtr lq_chain_sign_head(ChainHandle chain, ReadOnlySpan<byte> key, nuint keyLength, out IntPtr error);

    [LibraryImport(Library)] internal static partial void lq_chain_free(IntPtr chain);

    // ---- logger ----------------------------------------------------------------------------------------------------
    [LibraryImport(Library, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial IntPtr lq_logger_new(string? configJson, out IntPtr error);

    [LibraryImport(Library, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial IntPtr lq_logger_child(LoggerHandle parent, string suffix, string? fixedMetaJson, out IntPtr error);

    [LibraryImport(Library, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial int lq_log(
        LoggerHandle logger, string level, string? loggerName, string message, string? metaJson, string? llmJson,
        out IntPtr record, out IntPtr error);

    [LibraryImport(Library, StringMarshalling = StringMarshalling.Utf8)]
    internal static partial int lq_logger_set_level(LoggerHandle logger, string level, out IntPtr error);

    [LibraryImport(Library)] internal static partial int lq_logger_flush(LoggerHandle logger, uint timeoutMs);
    [LibraryImport(Library)] internal static partial int lq_logger_close(LoggerHandle logger, uint timeoutMs);
    [LibraryImport(Library)] internal static partial void lq_logger_free(IntPtr logger);

    // ---- helpers ---------------------------------------------------------------------------------------------------
    /// <summary>Reads and frees an owned C string.</summary>
    internal static string Take(IntPtr text)
    {
        try
        {
            return Marshal.PtrToStringUTF8(text) ?? string.Empty;
        }
        finally
        {
            lq_string_free(text);
        }
    }

    /// <summary>The message a failed call left in <paramref name="error"/> (freed), or <paramref name="fallback"/>.</summary>
    internal static string ErrorText(IntPtr error, string fallback = "the call failed") =>
        error == IntPtr.Zero ? fallback : Take(error);
}

/// <summary>An <c>lq_logger*</c>, freed when the last reference goes (also if <see cref="IDisposable.Dispose"/> was never called).</summary>
internal sealed class LoggerHandle : SafeHandle
{
    public LoggerHandle() : base(IntPtr.Zero, ownsHandle: true) { }

    public LoggerHandle(IntPtr handle) : base(IntPtr.Zero, ownsHandle: true) => SetHandle(handle);

    public override bool IsInvalid => handle == IntPtr.Zero;

    protected override bool ReleaseHandle()
    {
        Native.lq_logger_free(handle);
        return true;
    }
}

/// <summary>An <c>lq_chain*</c>.</summary>
internal sealed class ChainHandle : SafeHandle
{
    public ChainHandle() : base(IntPtr.Zero, ownsHandle: true) { }

    public ChainHandle(IntPtr handle) : base(IntPtr.Zero, ownsHandle: true) => SetHandle(handle);

    public override bool IsInvalid => handle == IntPtr.Zero;

    protected override bool ReleaseHandle()
    {
        Native.lq_chain_free(handle);
        return true;
    }
}
