#!/bin/sh
# The gate before publishing: builds the library as a jar that carries the release native library (under JNA's <os>-<arch>/
# resource path), publishes it to a throwaway local Maven repository, checks what is inside, and has a fresh Gradle app depend
# on it by coordinates and run it with LOGQUILL_LIB unset, the way a consumer does. Nothing is published to a real registry;
# everything happens in a scratch directory.
#   cargo build -p logquill-ffi --release && bindings/jvm/scripts/pack-smoke.sh
set -eu
export JAVA_HOME="${JAVA_HOME:-/opt/homebrew/opt/openjdk@21}"
export PATH="$JAVA_HOME/bin:/opt/homebrew/bin:$PATH"

here=$(cd "$(dirname "$0")" && pwd)
jvm=$(cd "$here/.." && pwd)
repo=$(cd "$jvm/../.." && pwd)

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) platform=darwin-aarch64; lib=liblogquill_ffi.dylib ;;
  Darwin-x86_64) platform=darwin-x86-64; lib=liblogquill_ffi.dylib ;;
  Linux-x86_64) platform=linux-x86-64; lib=liblogquill_ffi.so ;;
  Linux-aarch64) platform=linux-aarch64; lib=liblogquill_ffi.so ;;
  *) echo "pack-smoke: no JNA platform name for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac
built="$repo/target/release/$lib"
[ -f "$built" ] || { echo "pack-smoke: $built is missing; run 'cargo build -p logquill-ffi --release' first" >&2; exit 1; }

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

# publish from a copy, so the working tree gets no native/ directory and no build output
mkdir -p "$scratch/jvm"
(cd "$jvm" && tar --exclude=./build --exclude=./.gradle --exclude=./native -cf - .) | (cd "$scratch/jvm" && tar -xf -)
mkdir -p "$scratch/jvm/native/$platform"
cp "$built" "$scratch/jvm/native/$platform/"
gradle --no-daemon --console=plain -q -p "$scratch/jvm" publish >/dev/null

group_dir="$scratch/jvm/build/repo/io/logquill/logquill/3.0.0-rc.1"
jar="$group_dir/logquill-3.0.0-rc.1.jar"
[ -f "$jar" ] || { echo "pack-smoke: $jar was not published" >&2; exit 1; }
contents=$(unzip -Z1 "$jar")
echo "$contents" | grep -q "^$platform/$lib\$" || { echo "pack-smoke: the jar does not carry $platform/$lib" >&2; exit 1; }
echo "$contents" | grep -q '^io/logquill/Logger.class$' || { echo "pack-smoke: the jar has no io/logquill/Logger.class" >&2; exit 1; }
stray=$(echo "$contents" | grep -vE "^(io/logquill/|META-INF/|$platform/|io/$)" || true)
[ -z "$stray" ] || { echo "pack-smoke: files that should not ship: $stray" >&2; exit 1; }
grep -q '<artifactId>jna</artifactId>' "$group_dir/logquill-3.0.0-rc.1.pom" || { echo "pack-smoke: the POM does not declare jna" >&2; exit 1; }
[ -f "$group_dir/logquill-3.0.0-rc.1-sources.jar" ] || { echo "pack-smoke: no sources jar" >&2; exit 1; }

# a fresh app that depends on it by coordinates
app="$scratch/app"
mkdir -p "$app/src/main/java"
echo 'rootProject.name = "app"' > "$app/settings.gradle.kts"
cat > "$app/build.gradle.kts" <<EOF
plugins { application }
repositories {
    maven { url = uri("$scratch/jvm/build/repo") }
    mavenCentral()
}
dependencies { implementation("io.logquill:logquill:3.0.0-rc.1") }
java { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
application { mainClass.set("App") }
EOF
cat > "$app/src/main/java/App.java" <<'EOF'
import io.logquill.Core;
import io.logquill.Level;
import io.logquill.Logger;
import io.logquill.LoggerOptions;
import io.logquill.Plugins;
import java.util.Map;

public class App {
    public static void main(String[] args) {
        try (Logger log = new Logger(LoggerOptions.builder().plugin(Plugins.redact()).build())) {
            String record = log.logRecord(Level.INFO, "hello", Map.of("password", "p", "keep", 1), null);
            boolean redacted = record != null && record.contains("\"password\":\"***\"") && record.contains("\"keep\":1");
            System.out.println((redacted ? "ok " : "WRONG " + record + " ") + Core.canonicalJson("{\"b\":1,\"a\":2}"));
        }
    }
}
EOF
out=$(cd "$app" && env -u LOGQUILL_LIB gradle --no-daemon --console=plain -q run 2>&1) || { echo "$out" >&2; echo "pack-smoke: the app failed" >&2; exit 1; }
[ "$out" = 'ok {"a": 2, "b": 1}' ] || { echo "pack-smoke: the app printed: $out" >&2; exit 1; }

echo "pack-smoke ok: logquill-3.0.0-rc.1.jar ($(du -k "$jar" | cut -f1) KiB, $platform) published to a local Maven repository and used by coordinates from a fresh Gradle app"
