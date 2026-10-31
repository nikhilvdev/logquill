// swift-tools-version:5.9
import PackageDescription

// LogQuill for Swift: the Rust core through its C ABI. `CLogQuill.xcframework` is the static library plus header, built by
// `scripts/build-xcframework.sh` (untracked: it is a build product). A published release points the binary target at a
// zipped XCFramework attached to the release, with its checksum (`swift package compute-checksum`).
let package = Package(
    name: "LogQuill",
    platforms: [.macOS(.v12), .iOS(.v15)],
    products: [.library(name: "LogQuill", targets: ["LogQuill"])],
    targets: [
        .binaryTarget(name: "CLogQuill", path: "CLogQuill.xcframework"),
        .target(name: "LogQuill", dependencies: ["CLogQuill"]),
        // the checks are an executable, not an XCTest target: XCTest and Swift Testing need full Xcode (see README)
        .executableTarget(name: "LogQuillChecks", dependencies: ["LogQuill"]),
        // the README example, run by scripts/smoke.sh
        .executableTarget(name: "Quickstart", dependencies: ["LogQuill"]),
    ],
    swiftLanguageVersions: [.v5]
)
