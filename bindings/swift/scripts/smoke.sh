#!/bin/sh
# The gate before publishing: runs the README's example for real, checks the README contains it word for word, and builds
# a fresh Swift package that depends on this one by path and uses it. Needs the XCFramework (scripts/build-xcframework.sh).
# Everything happens in a scratch directory; nothing is published.
#   bindings/swift/scripts/smoke.sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
package=$(cd "$here/.." && pwd)
[ -d "$package/CLogQuill.xcframework" ] || { echo "smoke: CLogQuill.xcframework is missing; run scripts/build-xcframework.sh first" >&2; exit 1; }

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

# 1. the README contains the example verbatim
python3 - "$package" <<'PY'
import sys
root = sys.argv[1]
example = open(f"{root}/Sources/Quickstart/main.swift").read().strip()
readme = open(f"{root}/README.md").read()
if f"```swift\n{example}\n```" not in readme:
    sys.exit("smoke: README.md does not contain Sources/Quickstart/main.swift verbatim")
PY

# 2. the example runs and writes a redacted log
(cd "$scratch" && mkdir run && cd run && swift run --package-path "$package" --scratch-path "$scratch/build" Quickstart >/dev/null 2>"$scratch/run.err") \
  || { cat "$scratch/run.err" >&2; echo "smoke: the example failed" >&2; exit 1; }
log="$scratch/run/web.log"
[ -f "$log" ] || { echo "smoke: the example wrote no web.log" >&2; exit 1; }
[ "$(wc -l < "$log" | tr -d ' ')" = 2 ] || { echo "smoke: web.log should have 2 lines" >&2; exit 1; }
if grep -q -e hunter2 -e alice@example.com "$log"; then echo "smoke: a secret reached the log" >&2; exit 1; fi
grep -q '"password":"\*\*\*"' "$log" || { echo "smoke: the password was not redacted" >&2; exit 1; }
grep -q '"logger":"web.db"' "$log" || { echo "smoke: the child's name is missing" >&2; exit 1; }

# 3. a fresh package depending on this one by path
app="$scratch/app"
mkdir -p "$app/Sources/app"
cat > "$app/Package.swift" <<EOF
// swift-tools-version:5.9
import PackageDescription
let package = Package(
    name: "app",
    platforms: [.macOS(.v12)],
    dependencies: [.package(name: "LogQuill", path: "$package")],
    targets: [.executableTarget(name: "app", dependencies: [.product(name: "LogQuill", package: "LogQuill")])]
)
EOF
cat > "$app/Sources/app/main.swift" <<'EOF'
import LogQuill
var options = LoggerOptions()
options.plugins = [Plugins.redact()]
let log = try Logger(options)
let line = log.logRecord(.info, "hello", ["password": "p", "keep": 1]) ?? ""
print(line.contains("\"password\":\"***\"") && line.contains("\"keep\":1") ? "ok " + (try LogQuillCore.canonicalJSON("{\"b\":1,\"a\":2}")) : "WRONG " + line)
EOF
out=$(cd "$app" && swift run --scratch-path "$scratch/app-build" app 2>&1) || { echo "$out" >&2; echo "smoke: the app failed" >&2; exit 1; }
case "$out" in
  *'ok {"a": 2, "b": 1}') ;;
  *) echo "smoke: the app printed: $out" >&2; exit 1 ;;
esac

echo "smoke ok: the README example runs and is in the README; a fresh package depends on this one and uses it"
