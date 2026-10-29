import Foundation

/// Turns any Swift value into one `JSONSerialization` accepts, without ever raising: `JSONSerialization.data(withJSONObject:)`
/// throws an Objective-C exception (which Swift cannot catch, so it crashes the process) for NaN, infinities, non-string keys
/// and non-JSON types, and logging must never crash the app over what it was asked to log. The rules match the other bindings':
///
/// - `Date` becomes ISO 8601 (UTC, milliseconds); `URL`, `UUID` and `Data` become text (`Data` as base64);
/// - NaN and the infinities become the strings "NaN", "Infinity" and "-Infinity";
/// - dictionary keys that are not strings become their description; sets become arrays;
/// - anything else becomes `String(describing:)`, and nesting deeper than 50 becomes "<too deep>".
enum JSONSafe {
    static let depthLimit = 50

    static func sanitize(_ value: Any?, depth: Int = 0) -> Any {
        if depth > depthLimit { return "<too deep>" }
        guard let value else { return NSNull() }
        if value is NSNull { return NSNull() }
        if let number = value as? NSNumber {
            if CFGetTypeID(number) == CFBooleanGetTypeID() { return number.boolValue }
            if CFNumberIsFloatType(number) {
                let double = number.doubleValue
                if double.isNaN { return "NaN" }
                if double.isInfinite { return double > 0 ? "Infinity" : "-Infinity" }
            }
            return number
        }
        if let text = value as? String { return text }
        if let date = value as? Date { return iso.string(from: date) }
        if let url = value as? URL { return url.absoluteString }
        if let uuid = value as? UUID { return uuid.uuidString }
        if let data = value as? Data { return data.base64EncodedString() }
        if let dictionary = value as? [AnyHashable: Any] {
            var out: [String: Any] = [:]
            for (key, item) in dictionary { out[(key.base as? String) ?? String(describing: key.base)] = sanitize(item, depth: depth + 1) }
            return out
        }
        if let array = value as? [Any] { return array.map { sanitize($0, depth: depth + 1) } }
        if let set = value as? Set<AnyHashable> { return set.map { sanitize($0.base, depth: depth + 1) } }
        // an Optional wrapped in Any
        let mirror = Mirror(reflecting: value)
        if mirror.displayStyle == .optional { return sanitize(mirror.children.first?.value, depth: depth + 1) }
        return String(describing: value)
    }

    /// JSON text for any value; never throws.
    static func encode(_ value: Any?) -> String {
        let safe = sanitize(value)
        // wrapped in an array so a bare scalar is valid JSON for the serializer; the brackets are stripped again
        if let data = try? JSONSerialization.data(withJSONObject: [safe]) {
            let text = String(decoding: data, as: UTF8.self)
            return String(text.dropFirst().dropLast())
        }
        return "\"\(String(describing: safe))\""
    }

    /// JSON text for an object (a dictionary), or nil when there is none or it is empty.
    static func encodeObject(_ meta: [String: Any]?) -> String? {
        guard let meta, !meta.isEmpty else { return nil }
        return encode(meta)
    }

    private static let iso: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        formatter.timeZone = TimeZone(identifier: "UTC")
        return formatter
    }()
}
