import Foundation

/// Fields bound to every record logged inside a scope, by any `Logger`. It is a task-local value: it is there after an
/// `await`, in child tasks created inside the scope (`async let`, task groups), and not in unrelated tasks. The call's own
/// fields win over the context.
///
///     LogContext.with(["request_id": "r-1"]) { log.info("handling") }
public enum LogContext {
    @TaskLocal static var current: [String: Any] = [:]

    /// The fields bound right now (empty outside any scope).
    public static var fields: [String: Any] { current }

    /// Runs `operation` with `fields` added to the context.
    public static func with<T>(_ fields: [String: Any], operation: () throws -> T) rethrows -> T {
        try $current.withValue(current.merging(fields) { _, new in new }, operation: operation)
    }

    /// The `async` form of ``with(_:operation:)``.
    public static func with<T>(_ fields: [String: Any], operation: () async throws -> T) async rethrows -> T {
        try await $current.withValue(current.merging(fields) { _, new in new }, operation: operation)
    }
}
