#!/bin/sh
# Writes the tree of the Swift mirror repository for a release. SwiftPM resolves versions from git tags on a repository whose
# root holds Package.swift, and a binary target must name the zip's download URL and checksum inside that tagged Package.swift;
# the checksum only exists once the XCFramework is built, so the monorepo's own tag cannot carry it. The mirror holds the library
# alone (no checks executable, no quickstart) and points CLogQuill at the zip attached to the mirror's release.
#   bindings/swift/scripts/prepare-mirror.sh OUT_DIR ZIP_URL CHECKSUM
# OUT_DIR must not exist yet or be empty; the release workflow commits its contents to the mirror and tags the commit.
set -eu
[ $# -eq 3 ] || { echo "usage: prepare-mirror.sh OUT_DIR ZIP_URL CHECKSUM" >&2; exit 2; }
url=$2
checksum=$3
case "$url" in https://*) ;; *) echo "prepare-mirror: ZIP_URL must be an https URL, got '$url'; a binary target cannot be fetched from anywhere else" >&2; exit 1 ;; esac
case "$checksum" in
  [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) [ ${#checksum} -eq 64 ] || { echo "prepare-mirror: CHECKSUM must be the 64 hex digits of 'swift package compute-checksum', got ${#checksum} characters" >&2; exit 1; } ;;
  *) echo "prepare-mirror: CHECKSUM must be the 64 hex digits of 'swift package compute-checksum', got '$checksum'" >&2; exit 1 ;;
esac
here=$(cd "$(dirname "$0")" && pwd)
package=$(cd "$here/.." && pwd)
mkdir -p "$1"
[ -z "$(ls -A "$1")" ] || { echo "prepare-mirror: $1 is not empty" >&2; exit 1; }
out=$(cd "$1" && pwd)

mkdir -p "$out/Sources"
cp -R "$package/Sources/LogQuill" "$out/Sources/LogQuill"
cp "$package/README.md" "$package/LICENSE" "$out/"
cat > "$out/Package.swift" <<EOF
// swift-tools-version:5.9
import PackageDescription

// LogQuill for Swift, released from github.com/nikhilvdev/logquill (bindings/swift). This repository holds only what SwiftPM
// resolves: the library sources and the binary target, whose zip and checksum are written by the release.
let package = Package(
    name: "LogQuill",
    platforms: [.macOS(.v12), .iOS(.v15)],
    products: [.library(name: "LogQuill", targets: ["LogQuill"])],
    targets: [
        .binaryTarget(name: "CLogQuill", url: "$url", checksum: "$checksum"),
        .target(name: "LogQuill", dependencies: ["CLogQuill"]),
    ],
    swiftLanguageVersions: [.v5]
)
EOF
(cd "$out" && swift package dump-package >/dev/null) || { echo "prepare-mirror: the generated Package.swift does not parse" >&2; exit 1; }
echo "prepare-mirror ok: $out ($(find "$out" -type f | wc -l | tr -d ' ') files) pointing at $url"
