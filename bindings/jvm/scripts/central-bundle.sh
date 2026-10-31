#!/bin/sh
# Builds the bundle the Maven Central Portal accepts: the jar (carrying every platform's native library under JNA's <os>-<arch>/
# resource paths), the sources and Javadoc jars, the POM and Gradle module file, each with an MD5, SHA-1 and SHA-256 checksum
# and a detached GPG signature, in the Maven repository layout, zipped.
#   bindings/jvm/scripts/central-bundle.sh LIBS_DIR OUT_DIR
# LIBS_DIR holds <id>/<library file> as the release workflow's native build uploads them. Signing uses the default secret key of the
# running GPG home; a passphrase goes in GPG_PASSPHRASE. Central rejects unsigned files, so this fails unless every file is signed;
# LQ_ALLOW_UNSIGNED=1 skips signing for a local test of the layout only. Nothing is uploaded: that is the workflow's job.
set -eu
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@21}"
export PATH="$JAVA_HOME/bin:/opt/homebrew/bin:$PATH"

[ $# -eq 2 ] || { echo "usage: central-bundle.sh LIBS_DIR OUT_DIR" >&2; exit 2; }
libs=$(cd "$1" && pwd)
mkdir -p "$2"
out=$(cd "$2" && pwd)
here=$(cd "$(dirname "$0")" && pwd)
jvm=$(cd "$here/.." && pwd)

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/jvm"
(cd "$jvm" && tar --exclude=./build --exclude=./.gradle --exclude=./native --exclude=./bin -cf - .) | (cd "$scratch/jvm" && tar -xf -)

found=""
for dir in "$libs"/*/; do
  id=$(basename "$dir")
  case "$id" in
    linux-x64) platform=linux-x86-64; lib=liblogquill_ffi.so ;;
    linux-arm64) platform=linux-aarch64; lib=liblogquill_ffi.so ;;
    macos-arm64) platform=darwin-aarch64; lib=liblogquill_ffi.dylib ;;
    macos-x64) platform=darwin-x86-64; lib=liblogquill_ffi.dylib ;;
    windows-x64) platform=win32-x86-64; lib=logquill_ffi.dll ;;
    *) echo "central-bundle: no JNA platform for '$id'; known: linux-x64 linux-arm64 macos-arm64 macos-x64 windows-x64" >&2; exit 1 ;;
  esac
  [ -f "$dir$lib" ] || { echo "central-bundle: $dir$lib is missing; the native build for $id did not upload $lib" >&2; exit 1; }
  mkdir -p "$scratch/jvm/native/$platform"
  cp "$dir$lib" "$scratch/jvm/native/$platform/"
  found="$found $platform/$lib"
done
[ -n "$found" ] || { echo "central-bundle: $libs holds no platform directories" >&2; exit 1; }

gradle --no-daemon --console=plain -q -p "$scratch/jvm" publish >/dev/null
version_dir=$(find "$scratch/jvm/build/repo" -mindepth 5 -maxdepth 5 -type d -path '*/io/github/nikhilvdev/logquill/*' | head -n 1)
[ -d "$version_dir" ] || { echo "central-bundle: Gradle published nothing under build/repo" >&2; exit 1; }
version=$(basename "$version_dir")
# Gradle writes its own checksums and a maven-metadata file; Central wants exactly MD5, SHA-1 and SHA-256 of each artifact, made below
find "$scratch/jvm/build/repo" \( -name 'maven-metadata*' -o -name '*.md5' -o -name '*.sha1' -o -name '*.sha256' -o -name '*.sha512' \) -delete

for want in "logquill-$version.jar" "logquill-$version.pom" "logquill-$version-sources.jar" "logquill-$version-javadoc.jar"; do
  [ -f "$version_dir/$want" ] || { echo "central-bundle: $want was not built" >&2; exit 1; }
done
for entry in $found; do
  unzip -Z1 "$version_dir/logquill-$version.jar" | grep -qx "$entry" || { echo "central-bundle: the jar does not carry $entry" >&2; exit 1; }
done

for file in "$version_dir"/*; do
  case "$file" in *.asc|*.md5|*.sha1|*.sha256) continue ;; esac
  if [ "${LQ_ALLOW_UNSIGNED:-}" != 1 ]; then
    printf '%s' "${GPG_PASSPHRASE:-}" | gpg --batch --yes --pinentry-mode loopback --passphrase-fd 0 --armor --detach-sign --output "$file.asc" "$file" \
      || { echo "central-bundle: could not sign $(basename "$file"); import the key (gpg --import) and set GPG_PASSPHRASE" >&2; exit 1; }
  fi
  openssl md5 -r "$file" | cut -d' ' -f1 > "$file.md5"
  shasum -a 1 "$file" | cut -d' ' -f1 > "$file.sha1"
  shasum -a 256 "$file" | cut -d' ' -f1 > "$file.sha256"
done

bundle="$out/logquill-$version-bundle.zip"
rm -f "$bundle"
(cd "$scratch/jvm/build/repo" && zip -qr "$bundle" io)
count=$(unzip -Z1 "$bundle" | grep -c -v '/$')
echo "central-bundle ok: $(basename "$bundle") ($(du -k "$bundle" | cut -f1) KiB, $count files) with natives:$found"
