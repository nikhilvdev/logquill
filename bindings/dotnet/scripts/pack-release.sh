#!/bin/sh
# Packs the NuGet package for a release: one .nupkg carrying every platform's native library under runtimes/<rid>/native/.
#   bindings/dotnet/scripts/pack-release.sh LIBS_DIR OUT_DIR
# LIBS_DIR holds <id>/<library file> as the release workflow's native build uploads them (linux-x64/liblogquill_ffi.so,
# macos-arm64/liblogquill_ffi.dylib, windows-x64/logquill_ffi.dll, ...). Packs from a scratch copy, so the working tree gets
# no runtimes/ directory. Fails if a platform directory has no library, or the package does not hold every one it was given.
set -eu
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1

[ $# -eq 2 ] || { echo "usage: pack-release.sh LIBS_DIR OUT_DIR" >&2; exit 2; }
libs=$(cd "$1" && pwd)
mkdir -p "$2"
out=$(cd "$2" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
dotnet_dir=$(cd "$here/.." && pwd)

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/dotnet"
cp -R "$dotnet_dir/src" "$dotnet_dir/README.md" "$scratch/dotnet/"
rm -rf "$scratch/dotnet/src/LogQuill/bin" "$scratch/dotnet/src/LogQuill/obj" "$scratch/dotnet/src/LogQuill/runtimes"

expected=""
for dir in "$libs"/*/; do
  id=$(basename "$dir")
  case "$id" in
    linux-x64) rid=linux-x64; lib=liblogquill_ffi.so ;;
    linux-arm64) rid=linux-arm64; lib=liblogquill_ffi.so ;;
    macos-arm64) rid=osx-arm64; lib=liblogquill_ffi.dylib ;;
    macos-x64) rid=osx-x64; lib=liblogquill_ffi.dylib ;;
    windows-x64) rid=win-x64; lib=logquill_ffi.dll ;;
    *) echo "pack-release: no runtime identifier for '$id'; known: linux-x64 linux-arm64 macos-arm64 macos-x64 windows-x64" >&2; exit 1 ;;
  esac
  [ -f "$dir$lib" ] || { echo "pack-release: $dir$lib is missing; the native build for $id did not upload $lib" >&2; exit 1; }
  mkdir -p "$scratch/dotnet/src/LogQuill/runtimes/$rid/native"
  cp "$dir$lib" "$scratch/dotnet/src/LogQuill/runtimes/$rid/native/"
  expected="$expected runtimes/$rid/native/$lib"
done
[ -n "$expected" ] || { echo "pack-release: $libs holds no platform directories" >&2; exit 1; }

dotnet pack "$scratch/dotnet/src/LogQuill/LogQuill.csproj" -c Release -o "$out" >/dev/null
package=$(ls "$out"/LogQuill.*.nupkg | grep -v '\.symbols\.' | tail -n 1)
contents=$(unzip -Z1 "$package")
for entry in $expected lib/net8.0/LogQuill.dll lib/net10.0/LogQuill.dll; do
  echo "$contents" | grep -qx "$entry" || { echo "pack-release: $package does not hold $entry" >&2; exit 1; }
done
unzip -p "$package" LogQuill.nuspec | grep -q '<license type="expression">MIT</license>' || { echo "pack-release: $package does not declare the MIT licence" >&2; exit 1; }
echo "pack-release ok: $(basename "$package") ($(du -k "$package" | cut -f1) KiB) with:$expected"
