import CLogQuill
import Foundation

/// Thrown by the core's helpers and by `Logger.init` for a config the core rejects; the message says what is wrong.
public struct LogQuillError: Error, CustomStringConvertible, LocalizedError {
    public let message: String
    public init(_ message: String) { self.message = message }
    public var description: String { message }
    public var errorDescription: String? { message }
}

/// What verifying a hash chain found.
public struct Verification: Equatable, Sendable {
    public let ok: Bool
    public let recordsChecked: Int
    /// The 1-based position of the first record that failed, if any.
    public let brokenAt: Int?
    public let reason: String?
    public let headHash: String?
}

/// Takes ownership of an owned C string from the library and frees it.
func take(_ pointer: UnsafeMutablePointer<CChar>) -> String {
    defer { lq_string_free(pointer) }
    return String(cString: pointer)
}

/// The message a failed call left in `error`, freed.
func failure(_ error: UnsafeMutablePointer<CChar>?, _ fallback: String = "the call failed") -> LogQuillError {
    LogQuillError(error.map(take) ?? fallback)
}

/// The core's stateless helpers. ``call(_:_:)`` runs any of them by the name the Node addon uses (`canonicalJson`,
/// `verifyChain`, `piiRedactText`, ...); the typed functions cover the common ones. An argument that is a record, meta or list
/// of records may be a value, or a `String` holding its JSON text: **pass text when a number's spelling matters**, since `1` and
/// `1.0` hash differently in a tamper-evident chain.
public enum LogQuillCore {
    /// The library's version, e.g. `3.0.0-dev.0`.
    public static var version: String { String(cString: lq_version()) }

    /// Runs the helper `name` and returns its result as JSON text. Throws `LogQuillError` with the core's message.
    public static func callRaw(_ name: String, _ arguments: [Any?] = []) throws -> String {
        let wire = arguments.map { argument -> Any in
            if let data = argument as? Data { return data.base64EncodedString() } // binary keys cross as base64
            return JSONSafe.sanitize(argument)
        }
        let json = String(decoding: try JSONSerialization.data(withJSONObject: wire), as: UTF8.self)
        var error: UnsafeMutablePointer<CChar>?
        guard let result = lq_call(name, json, &error) else { throw failure(error) }
        return take(result)
    }

    /// Runs the helper `name` and returns its result as a Foundation value (a `String`, `NSNumber`, array or dictionary).
    public static func call(_ name: String, _ arguments: [Any?] = []) throws -> Any {
        let text = try callRaw(name, arguments)
        return try JSONSerialization.jsonObject(with: Data(text.utf8), options: .fragmentsAllowed)
    }

    private static func string(_ name: String, _ arguments: [Any?]) throws -> String {
        guard let text = try call(name, arguments) as? String else { throw LogQuillError("\(name) did not return text") }
        return text
    }

    /// The canonical text of a JSON document: what the tamper chain hashes.
    public static func canonicalJSON(_ json: String) throws -> String { try string("canonicalJson", [json]) }

    /// `text` with emails, card numbers, phone numbers, SSNs and API keys replaced (`"***"` unless `replacement` is given).
    public static func piiRedactText(_ text: String, replacement: String? = nil) throws -> String {
        try string("piiRedactText", [text, replacement])
    }

    /// A copy of `meta` (a dictionary or its JSON text) with the values of secret-named keys replaced, as JSON text.
    public static func redactMeta(_ meta: Any, options: [String: Any]? = nil) throws -> String {
        try callRaw("redactMeta", options.map { [meta, $0] } ?? [meta])
    }

    /// Verifies a hash-chained list of records (JSON text of an array, or any list of records).
    public static func verifyChain(_ records: Any, genesis: String? = nil) throws -> Verification {
        try verification(try call("verifyChain", [records, genesis]))
    }

    /// Verifies a chain and that its head matches `signature`; catches a deleted last line.
    public static func verifySignedChain(_ records: Any, key: Data, signature: String, genesis: String? = nil) throws -> Verification {
        try verification(try call("verifySignedChain", [records, key, signature, genesis]))
    }

    /// HMAC-SHA256 of a chain head under `key`, hex-encoded.
    public static func signHead(_ headHash: String, key: Data) throws -> String { try string("signHead", [headHash, key]) }

    /// Whether `signature` is what ``signHead(_:key:)`` gives (compared in constant time).
    public static func verifyHeadSignature(_ headHash: String, key: Data, signature: String) throws -> Bool {
        (try call("verifyHeadSignature", [headHash, key, signature]) as? Bool) ?? false
    }

    /// The hash a chain starts from.
    public static var genesisHash: String { (try? string("genesisHash", [])) ?? "" }

    private static func verification(_ value: Any) throws -> Verification {
        guard let object = value as? [String: Any], let ok = object["ok"] as? Bool else { throw LogQuillError("unexpected verification result") }
        return Verification(
            ok: ok, recordsChecked: object["recordsChecked"] as? Int ?? 0, brokenAt: object["brokenAt"] as? Int,
            reason: object["reason"] as? String, headHash: object["headHash"] as? String)
    }
}

/// A hash chain: each record stamped through it carries `meta.prev_hash` and `meta.hash`, so a log can be checked later for an
/// edited or removed line (``LogQuillCore/verifyChain(_:genesis:)``). Safe to share between threads.
public final class Chain: @unchecked Sendable {
    private let handle: OpaquePointer

    /// Starts a chain from `genesis` (nil: the standard genesis hash).
    public init(genesis: String? = nil) throws {
        var error: UnsafeMutablePointer<CChar>?
        guard let handle = lq_chain_new(genesis, &error) else { throw failure(error) }
        self.handle = handle
    }

    deinit { lq_chain_free(handle) }

    /// Chains `record` (a record as JSON text) and returns it with the hashes set, as JSON text.
    public func stamp(_ record: String) throws -> String {
        var error: UnsafeMutablePointer<CChar>?
        guard let result = lq_chain_stamp(handle, record, &error) else { throw failure(error) }
        return take(result)
    }

    /// The hash of the last record chained.
    public func head() throws -> String {
        var error: UnsafeMutablePointer<CChar>?
        guard let result = lq_chain_head(handle, &error) else { throw failure(error) }
        return take(result)
    }

    /// HMAC-SHA256 of the head under `key`, hex-encoded: a signature that a deleted last line breaks.
    public func signHead(key: Data) throws -> String {
        var error: UnsafeMutablePointer<CChar>?
        let result = key.withUnsafeBytes { buffer in
            lq_chain_sign_head(handle, buffer.bindMemory(to: UInt8.self).baseAddress, buffer.count, &error)
        }
        guard let result else { throw failure(error) }
        return take(result)
    }
}
