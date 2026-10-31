using System.Collections;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace LogQuill;

/// <summary>
/// Turns any .NET value into JSON text without throwing: logging must never crash the application over what it was asked to
/// log. Objects serialize with snake_case property names (the record contract's spelling); NaN and the infinities become the
/// strings "NaN", "Infinity" and "-Infinity"; a cycle is cut; a value that still cannot be serialized (a getter that throws,
/// text with a lone surrogate) is replaced by its <see cref="object.ToString"/>, or "&lt;unprintable T&gt;" if that throws.
/// </summary>
internal static class Json
{
    internal static readonly JsonSerializerOptions Options = new()
    {
        ReferenceHandler = ReferenceHandler.IgnoreCycles,
        NumberHandling = JsonNumberHandling.AllowNamedFloatingPointLiterals,
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        MaxDepth = 64,
        IncludeFields = false,
    };

    /// <summary>JSON text for <paramref name="value"/>; never throws.</summary>
    internal static string Dump(object? value)
    {
        try
        {
            return JsonSerializer.Serialize(value, Options);
        }
        catch (Exception)
        {
            return JsonSerializer.Serialize(Printable(value), Options);
        }
    }

    /// <summary>
    /// <paramref name="meta"/> as a JSON object, or null when there is none. A property or entry that cannot be serialized
    /// becomes its text; the rest are kept.
    /// </summary>
    internal static string? DumpObject(object? meta)
    {
        if (meta is null)
        {
            return null;
        }

        try
        {
            var element = JsonSerializer.SerializeToElement(meta, Options);
            return element.ValueKind == JsonValueKind.Object ? element.GetRawText() : Dump(new Dictionary<string, object?> { ["value"] = meta });
        }
        catch (Exception)
        {
            var salvaged = new Dictionary<string, object?>();
            foreach (var (key, value) in Entries(meta))
            {
                try
                {
                    salvaged[key] = JsonSerializer.SerializeToElement(value, Options);
                }
                catch (Exception)
                {
                    salvaged[key] = Printable(value);
                }
            }

            return Dump(salvaged);
        }
    }

    private static IEnumerable<(string Key, object? Value)> Entries(object meta)
    {
        if (meta is IDictionary dictionary)
        {
            foreach (DictionaryEntry entry in dictionary)
            {
                yield return (Printable(entry.Key), entry.Value);
            }

            yield break;
        }

        foreach (var property in meta.GetType().GetProperties().Where(p => p.GetIndexParameters().Length == 0 && p.CanRead))
        {
            object? value;
            try
            {
                value = property.GetValue(meta);
            }
            catch (Exception)
            {
                value = $"<{property.Name} threw>";
            }

            yield return (JsonNamingPolicy.SnakeCaseLower.ConvertName(property.Name), value);
        }
    }

    internal static string Printable(object? value)
    {
        try
        {
            return Scrub(value?.ToString() ?? "null");
        }
        catch (Exception)
        {
            return $"<unprintable {value?.GetType().Name}>";
        }
    }

    /// <summary>Replaces lone UTF-16 surrogates (which have no UTF-8 form) with U+FFFD.</summary>
    internal static string Scrub(string text)
    {
        for (var i = 0; i < text.Length; i++)
        {
            var c = text[i];
            if (char.IsHighSurrogate(c) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1]))
            {
                i++;
            }
            else if (char.IsSurrogate(c))
            {
                return string.Create(text.Length, text, static (span, source) =>
                {
                    for (var j = 0; j < source.Length; j++)
                    {
                        var d = source[j];
                        if (char.IsHighSurrogate(d) && j + 1 < source.Length && char.IsLowSurrogate(source[j + 1]))
                        {
                            span[j] = d;
                            span[++j] = source[j];
                        }
                        else
                        {
                            span[j] = char.IsSurrogate(d) ? '�' : d;
                        }
                    }
                });
            }
        }

        return text;
    }
}
