#!/bin/sh
# Builds CLogQuill.xcframework: the C ABI (crates/logquill-ffi, include/logquill.h) as a static library plus its headers and
# a clang module map, the shape SwiftPM's `.binaryTarget` takes. With full Xcode it uses `xcodebuild -create-xcframework`;
# with only the Command Line Tools it writes the same layout by hand (an XCFramework for a library is a directory with an
# Info.plist, so there is nothing xcodebuild adds). Builds for the machine's own architecture; a release builds each Apple
# platform and passes them all to xcodebuild.
#   bindings/swift/scripts/build-xcframework.sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
package=$(cd "$here/.." && pwd)
repo=$(cd "$package/../.." && pwd)

cargo build --manifest-path "$repo/Cargo.toml" -p logquill-ffi --release
library="$repo/target/release/liblogquill_ffi.a"
[ -f "$library" ] || { echo "build-xcframework: $library was not built" >&2; exit 1; }

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/Headers"
cp "$repo/crates/logquill-ffi/include/logquill.h" "$scratch/Headers/"
cat > "$scratch/Headers/module.modulemap" <<'EOF'
module CLogQuill {
    header "logquill.h"
    export *
}
EOF

output="$package/CLogQuill.xcframework"
rm -rf "$output"
if xcodebuild -version >/dev/null 2>&1; then
  xcodebuild -create-xcframework -library "$library" -headers "$scratch/Headers" -output "$output"
else
  case "$(uname -m)" in arm64) arch=arm64 ;; *) arch=x86_64 ;; esac
  identifier="macos-$arch"
  mkdir -p "$output/$identifier"
  cp "$library" "$output/$identifier/liblogquill_ffi.a"
  cp -R "$scratch/Headers" "$output/$identifier/Headers"
  cat > "$output/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>AvailableLibraries</key>
	<array>
		<dict>
			<key>BinaryPath</key>
			<string>liblogquill_ffi.a</string>
			<key>HeadersPath</key>
			<string>Headers</string>
			<key>LibraryIdentifier</key>
			<string>$identifier</string>
			<key>LibraryPath</key>
			<string>liblogquill_ffi.a</string>
			<key>SupportedArchitectures</key>
			<array>
				<string>$arch</string>
			</array>
			<key>SupportedPlatform</key>
			<string>macos</string>
		</dict>
	</array>
	<key>CFBundlePackageType</key>
	<string>XFWK</string>
	<key>XCFrameworkFormatVersion</key>
	<string>1.0</string>
</dict>
</plist>
EOF
fi
echo "wrote $output ($(du -sk "$output" | cut -f1) KiB)"
