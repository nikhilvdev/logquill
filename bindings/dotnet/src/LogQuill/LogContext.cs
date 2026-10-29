namespace LogQuill;

/// <summary>
/// Context bound to every record logged inside a scope, by any <see cref="Logger"/>. It flows with async code (it is an
/// <see cref="AsyncLocal{T}"/>), so it is there after an <c>await</c> and in tasks started inside the scope, and not on
/// unrelated threads. The call's own fields win over the context.
/// </summary>
public static class LogContext
{
    private static readonly AsyncLocal<IReadOnlyDictionary<string, object?>?> Current = new();

    /// <summary>
    /// Binds <paramref name="fields"/> (an anonymous object or a dictionary) until the returned scope is disposed:
    /// <c>using (LogContext.Push(new { RequestId = id })) { ... }</c>.
    /// </summary>
    public static IDisposable Push(object fields)
    {
        var previous = Current.Value;
        var merged = new Dictionary<string, object?>(previous ?? new Dictionary<string, object?>());
        var json = Json.DumpObject(fields);
        if (json is not null)
        {
            using var document = System.Text.Json.JsonDocument.Parse(json);
            foreach (var property in document.RootElement.EnumerateObject())
            {
                merged[property.Name] = property.Value.Clone();
            }
        }

        Current.Value = merged;
        return new Scope(previous);
    }

    /// <summary>The fields bound right now (empty outside any scope).</summary>
    public static IReadOnlyDictionary<string, object?> Fields => Current.Value ?? new Dictionary<string, object?>();

    private sealed class Scope(IReadOnlyDictionary<string, object?>? previous) : IDisposable
    {
        private bool _disposed;

        public void Dispose()
        {
            if (!_disposed)
            {
                _disposed = true;
                Current.Value = previous;
            }
        }
    }
}
