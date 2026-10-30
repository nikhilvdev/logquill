#!/bin/sh
# Builds the XCFramework a release publishes: macOS (arm64 + x86_64 in one library), iOS devices (arm64) and the iOS simulator
# (arm64 + x86_64 in one library), zipped with the .xcframework at the archive root, and the checksum SwiftPM needs to pin it.
#   bindings/swift/scripts/release-xcframework.sh OUT_DIR
# Writes OUT_DIR/CLogQuill.xcframework.zip and OUT_DIR/CLogQuill.xcframework.zip.checksum. Needs full Xcode (xcodebuild,
# lipo) and `rustup target add` for the five Apple targets below; the release workflow's macOS runner has both.
# LQ_HOST_ONLY=1 builds only the machine's own slice with the development script, to test the zip and checksum steps where
# Xcode is not installed; a host-only archive must never be published.
set -eu
[ $# -eq 1 ] || { echo "usage: release-xcframework.sh OUT_DIR" >&2; exit 2; }
mkdir -p "$1"
out=$(cd "$1" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
package=$(cd "$here/.." && pwd)
repo=$(cd "$package/../.." && pwd)

if [ "${LQ_HOST_ONLY:-}" = 1 ]; then
  "$here/build-xcframework.sh" >/dev/null
  framework="$package/CLogQuill.xcframework"
else
  command -v xcodebuild >/dev/null 2>&1 && xcodebuild -version >/dev/null 2>&1 \
    || { echo "release-xcframework: full Xcode is required (xcodebuild -create-xcframework builds the multi-platform framework); install it or run this on a macOS runner" >&2; exit 1; }
  targets="aarch64-apple-darwin x86_64-apple-darwin aarch64-apple-ios aarch64-apple-ios-sim x86_64-apple-ios"
  for target in $targets; do
    rustup target list --installed | grep -qx "$target" || { echo "release-xcframework: the Rust target $target is missing; run: rustup target add $target" >&2; exit 1; }
    cargo build --manifest-path "$repo/Cargo.toml" -p logquill-ffi --release --target "$target"
  done
  scratch=$(mktemp -d)
  trap 'rm -rf "$scratch"' EXIT
  built() { echo "$repo/target/$1/release/liblogquill_ffi.a"; }
  mkdir -p "$scratch/macos" "$scratch/ios-sim" "$scratch/Headers"
  lipo -create "$(built aarch64-apple-darwin)" "$(built x86_64-apple-darwin)" -output "$scratch/macos/liblogquill_ffi.a"
  lipo -create "$(built aarch64-apple-ios-sim)" "$(built x86_64-apple-ios)" -output "$scratch/ios-sim/liblogquill_ffi.a"
  cp "$repo/crates/logquill-ffi/include/logquill.h" "$scratch/Headers/"
  printf 'module CLogQuill {\n    header "logquill.h"\n    export *\n}\n' > "$scratch/Headers/module.modulemap"
  framework="$scratch/CLogQuill.xcframework"
  xcodebuild -create-xcframework \
    -library "$scratch/macos/liblogquill_ffi.a" -headers "$scratch/Headers" \
    -library "$(built aarch64-apple-ios)" -headers "$scratch/Headers" \
    -library "$scratch/ios-sim/liblogquill_ffi.a" -headers "$scratch/Headers" \
    -output "$framework"
fi

[ -d "$framework" ] || { echo "release-xcframework: $framework was not built" >&2; exit 1; }
archive="$out/CLogQuill.xcframework.zip"
rm -f "$archive" "$archive.checksum"
(cd "$(dirname "$framework")" && zip -qr -X "$archive" CLogQuill.xcframework)
unzip -Z1 "$archive" | head -n 1 | grep -q '^CLogQuill.xcframework/' || { echo "release-xcframework: the .xcframework is not at the archive root" >&2; exit 1; }
(cd "$package" && swift package compute-checksum "$archive") > "$archive.checksum"
echo "release-xcframework ok: $(basename "$archive") ($(du -k "$archive" | cut -f1) KiB), checksum $(cat "$archive.checksum")"
