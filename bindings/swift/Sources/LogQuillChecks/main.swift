// The Swift binding's checks, as an executable: `swift run LogQuillChecks`. They are not an XCTest / Swift Testing target
// because neither framework is in the Command Line Tools (only in full Xcode), and these checks should run wherever Swift
// does, including a machine with no Xcode. Exit code 0 when every check passes.
import Foundation
@testable import LogQuill

// ---- a tiny harness --------------------------------------------------------------------------------------------------
var total = 0
var failures: [String] = []
var currentTest = ""

func fail(_ message: String, _ line: UInt) { failures.append("\(currentTest) (line \(line)): \(message)") }
func check(_ condition: Bool, _ what: @autoclosure () -> String = "", line: UInt = #line) {
    total += 1
    if !condition { fail("check failed \(what())", line) }
}
func equal<T: Equatable>(_ got: T, _ want: T, line: UInt = #line) {
    total += 1
    if got != want { fail("expected \(want), got \(got)", line) }
}
func throwsError(_ contains: String, line: UInt = #line, _ body: () throws -> Void) {
    total += 1
    do { try body(); fail("expected an error containing \"\(contains)\", got none", line) } catch {
        if !"\(error)".contains(contains) { fail("expected an error containing \"\(contains)\", got \(error)", line) }
    }
}
func test(_ name: String, _ body: () throws -> Void) {
    currentTest = name
    do { try body() } catch { fail("threw \(error)", 0) }
}
func asyncTest(_ name: String, _ body: () async throws -> Void) async {
    currentTest = name
    do { try await body() } catch { fail("threw \(error)", 0) }
}

func temporaryFile(_ name: String) -> String {
    FileManager.default.temporaryDirectory.appendingPathComponent("lq-swift-\(UUID().uuidString)-\(name)").path
}

final class Sink: HostTransport {
    var lines: [String] = []
    var records: [[String: Any]] = []
    func write(line: String, record: [String: Any]) { lines.append(line); records.append(record) }
}

func record(_ message: String) -> String {
    #"{"schema_version":"2.0","timestamp":"2026-01-01T00:00:00.000Z","level":"INFO","logger":"a","message":"\#(message)","meta":{}}"#
}

// ---- helpers ---------------------------------------------------------------------------------------------------------
test("text helpers return strings and keep a number's spelling") {
    equal(try LogQuillCore.canonicalJSON(#"{"b":1,"a":2}"#), #"{"a": 2, "b": 1}"#)
    check(try LogQuillCore.canonicalJSON(#"{"x": 1.0}"#) != (try LogQuillCore.canonicalJSON(#"{"x": 1}"#)))
    equal(try LogQuillCore.piiRedactText("mail alice@example.com"), "mail ***")
    check(try LogQuillCore.redactMeta(["password": "x", "n": 1]).contains(#""password":"***""#))
}

test("bad calls throw with the core's message") {
    throwsError("unknown helper") { _ = try LogQuillCore.call("noSuchHelper") }
    throwsError("not valid JSON") { _ = try LogQuillCore.canonicalJSON("not json") }
    throwsError("dialect") { _ = try LogQuillCore.call("createTableSql", ["oracle", "t"]) }
}

test("binary keys cross as bytes") {
    let key = Data([0xFF, 0x00, 0x6B])
    let signature = try LogQuillCore.signHead(LogQuillCore.genesisHash, key: key)
    check(try LogQuillCore.verifyHeadSignature(LogQuillCore.genesisHash, key: key, signature: signature))
    check(!(try LogQuillCore.verifyHeadSignature(LogQuillCore.genesisHash, key: Data("other".utf8), signature: signature)))
}

test("the shared canonical-json corpus") {
    var directory = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
    var corpus: URL?
    while directory.path != "/" {
        let candidate = directory.appendingPathComponent("schema/corpus/canonical-json.json")
        if FileManager.default.fileExists(atPath: candidate.path) { corpus = candidate; break }
        directory.deleteLastPathComponent()
    }
    guard let url = corpus else { check(false, "schema/corpus/canonical-json.json not found"); return }
    let cases = (try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any])?["cases"] as? [[String: Any]] ?? []
    check(!cases.isEmpty, "no cases")
    for entry in cases {
        let input = (entry["input"] as? [String: Any])?["json"] as? String ?? ""
        let expected = (entry["expected"] as? [String: Any])?["text"] as? String ?? ""
        equal(try LogQuillCore.canonicalJSON(input), expected)
    }
}

// ---- the chain -------------------------------------------------------------------------------------------------------
test("stamped records verify and an edit is found") {
    let chain = try Chain()
    var records = try ["a", "b", "c", "d"].map { try chain.stamp(record($0)) }
    let verdict = try LogQuillCore.verifyChain("[" + records.joined(separator: ",") + "]")
    check(verdict.ok)
    equal(verdict.recordsChecked, 4)
    equal(verdict.headHash, try chain.head())
    records[2] = records[2].replacingOccurrences(of: #""c""#, with: #""edited""#)
    let broken = try LogQuillCore.verifyChain("[" + records.joined(separator: ",") + "]")
    check(!broken.ok)
    equal(broken.brokenAt, 3)
}

test("a signed head catches a deleted last line") {
    let chain = try Chain()
    let records = try ["a", "b", "c"].map { try chain.stamp(record($0)) }
    let key = Data("secret".utf8)
    let signature = try chain.signHead(key: key)
    check(try LogQuillCore.verifySignedChain("[" + records.joined(separator: ",") + "]", key: key, signature: signature).ok)
    check(!(try LogQuillCore.verifySignedChain("[" + records.prefix(2).joined(separator: ",") + "]", key: key, signature: signature).ok))
    check(!(try LogQuillCore.verifySignedChain("[" + records.joined(separator: ",") + "]", key: Data("wrong".utf8), signature: signature).ok))
}

// ---- safe JSON -------------------------------------------------------------------------------------------------------
test("values that would crash JSONSerialization become text") {
    struct Custom { let id = 7 }
    let text = JSONSafe.encode([
        "nan": Double.nan, "inf": -Double.infinity, "when": Date(timeIntervalSince1970: 1_767_323_045),
        "url": URL(string: "https://example.com")!, "custom": Custom(), "keys": [1: "int key"] as [Int: String],
        "nested": [1, 2.5, NSNull(), true] as [Any],
    ] as [String: Any])
    let parsed = try JSONSerialization.jsonObject(with: Data(text.utf8)) as? [String: Any]
    equal(parsed?["nan"] as? String, "NaN")
    equal(parsed?["inf"] as? String, "-Infinity")
    equal(parsed?["when"] as? String, "2026-01-02T03:04:05.000Z")
    equal(parsed?["url"] as? String, "https://example.com")
    check((parsed?["custom"] as? String)?.contains("Custom") ?? false)
    equal((parsed?["keys"] as? [String: Any])?["1"] as? String, "int key")
    equal((parsed?["nested"] as? [Any])?.count, 4)
}

test("very deep nesting stops") {
    var deep: Any = "leaf"
    for _ in 0..<200 { deep = ["k": deep] }
    check(JSONSafe.encode(deep).contains("<too deep>"))
}

// ---- the logger ------------------------------------------------------------------------------------------------------
test("redaction happens before the file is written") {
    let path = temporaryFile("app.log")
    var options = LoggerOptions()
    options.plugins = [Plugins.redact(), Plugins.pii()]
    options.transports = [Transports.file(path: path)]
    let log = try Logger(options)
    check(log.info("signed in", ["password": "hunter2", "note": "mail alice@example.com"]))
    log.close()
    let text = try String(contentsOfFile: path, encoding: .utf8)
    check(!text.contains("hunter2") && !text.contains("alice@example.com"), text)
    let first = try JSONSerialization.jsonObject(with: Data(text.split(separator: "\n")[0].utf8)) as? [String: Any]
    equal((first?["meta"] as? [String: Any])?["password"] as? String, "***")
}

test("the level filters and a child shares it") {
    var options = LoggerOptions()
    options.level = .warn
    let log = try Logger(options)
    check(!log.info("below"))
    check(log.error("above"))
    let child = try log.child("db", ["component": "pg"])
    equal(child.name, "app.db")
    try log.setLevel(.error)
    check(!child.warn("filtered"))
}

test("a child adds fixed meta and an llm block is carried") {
    let sink = Sink()
    var options = LoggerOptions()
    options.hostTransports = [sink]
    let child = try Logger(options).child("db", ["component": "pg"])
    child.info("query", ["ms": 4], llm: ["model": "m", "tokens_in": 3])
    let record = sink.records.first ?? [:]
    equal(record["logger"] as? String, "app.db")
    equal((record["meta"] as? [String: Any])?["component"] as? String, "pg")
    equal((record["meta"] as? [String: Any])?["ms"] as? Int, 4)
    equal((record["llm"] as? [String: Any])?["model"] as? String, "m")
}

test("prompt content is withheld by default") {
    let log = try Logger()
    let line = log.logRecord(.info, "call", ["input_messages": [["role": "user", "content": "secret prompt"]]]) ?? ""
    check(!line.isEmpty && !line.contains("secret prompt"), line)
}

test("a wrong config throws naming the problem") {
    var bad = LoggerOptions()
    bad.contentPolicy = "loud"
    throwsError("content policy must be one of") { _ = try Logger(bad) }
    var plugin = LoggerOptions()
    plugin.plugins = [["type": "redactt"]]
    throwsError("unknown plugin type") { _ = try Logger(plugin) }
}

test("values that crash JSONSerialization never make a log call fail") {
    let sink = Sink()
    var options = LoggerOptions()
    options.hostTransports = [sink]
    let log = try Logger(options)
    check(log.info("odd", ["nan": Double.nan, "inf": Double.infinity, "when": Date(), "nul": "a\0b", "object": NSObject()]))
    let meta = sink.records.first?["meta"] as? [String: Any]
    equal(meta?["nan"] as? String, "NaN")
    equal(meta?["inf"] as? String, "Infinity")
    check(meta?["object"] != nil)
}

test("the tamper chain is applied as records are written") {
    let path = temporaryFile("audit.log")
    var options = LoggerOptions()
    options.tamperEvident = true
    options.transports = [Transports.file(path: path)]
    let log = try Logger(options)
    for i in 0..<10 { log.info("event \(i)", ["i": i]) }
    log.close()
    var lines = try String(contentsOfFile: path, encoding: .utf8).split(separator: "\n").map(String.init)
    check(try LogQuillCore.verifyChain("[" + lines.joined(separator: ",") + "]").ok)
    lines[4] = lines[4].replacingOccurrences(of: "event 4", with: "tampered")
    check(!(try LogQuillCore.verifyChain("[" + lines.joined(separator: ",") + "]").ok))
}

test("many threads log through one logger") {
    let path = temporaryFile("busy.log")
    var options = LoggerOptions()
    options.tamperEvident = true
    options.transports = [Transports.file(path: path)]
    let log = try Logger(options)
    DispatchQueue.concurrentPerform(iterations: 8) { thread in
        for i in 0..<100 { log.info("t\(thread) #\(i)") }
    }
    check(log.close())
    let lines = try String(contentsOfFile: path, encoding: .utf8).split(separator: "\n").map(String.init)
    equal(lines.count, 800)
    check(try LogQuillCore.verifyChain("[" + lines.joined(separator: ",") + "]").ok)
}

test("loggers that go out of scope are freed") {
    for i in 0..<300 {
        var options = LoggerOptions()
        options.name = "gc\(i)"
        _ = try Logger(options).child("c").info("x")
    }
    check(true)
}

await asyncTest("context flows across await and is gone outside the scope") {
    let sink = Sink()
    var options = LoggerOptions()
    options.hostTransports = [sink]
    let log = try Logger(options)
    await LogContext.with(["request_id": "r1", "user": "u"]) {
        try? await Task.sleep(nanoseconds: 1_000_000)
        log.info("in", ["user": "override"])
    }
    log.info("out")
    let inside = sink.records[0]["meta"] as? [String: Any]
    equal(inside?["request_id"] as? String, "r1")
    equal(inside?["user"] as? String, "override") // the call's own field wins
    check((sink.records[1]["meta"] as? [String: Any])?.isEmpty ?? false)
    check(LogContext.fields.isEmpty)
}

// ---- the result ------------------------------------------------------------------------------------------------------
if failures.isEmpty {
    print("LogQuillChecks ok: \(total) checks passed (logquill \(LogQuillCore.version))")
} else {
    for failure in failures { FileHandle.standardError.write(Data("FAIL \(failure)\n".utf8)) }
    FileHandle.standardError.write(Data("\(failures.count) of \(total) checks failed\n".utf8))
    exit(1)
}
