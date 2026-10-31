#!/bin/sh
# The gate before publishing: packs LogQuill as a NuGet package carrying the release native library under
# runtimes/<rid>/native/, checks what is inside, installs it from a local feed into a new console app, and runs it with
# LOGQUILL_LIB unset, the way a consumer does. Nothing is published; everything happens in a scratch directory.
#   cargo build -p logquill-ffi --release && bindings/dotnet/scripts/pack-smoke.sh
set -eu
export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1

here=$(cd "$(dirname "$0")" && pwd)
dotnet_dir=$(cd "$here/.." && pwd)
repo=$(cd "$dotnet_dir/../.." && pwd)

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) rid=osx-arm64; lib=liblogquill_ffi.dylib ;;
  Darwin-x86_64) rid=osx-x64; lib=liblogquill_ffi.dylib ;;
  Linux-x86_64) rid=linux-x64; lib=liblogquill_ffi.so ;;
  Linux-aarch64) rid=linux-arm64; lib=liblogquill_ffi.so ;;
  *) echo "pack-smoke: no runtime identifier for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac
built="$repo/target/release/$lib"
[ -f "$built" ] || { echo "pack-smoke: $built is missing; run 'cargo build -p logquill-ffi --release' first" >&2; exit 1; }

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

# pack from a copy, so the working tree gets no runtimes/ directory and no .nupkg
mkdir -p "$scratch/dotnet"
cp -R "$dotnet_dir/src" "$dotnet_dir/README.md" "$scratch/dotnet/"
rm -rf "$scratch/dotnet/src/LogQuill/bin" "$scratch/dotnet/src/LogQuill/obj"
mkdir -p "$scratch/dotnet/src/LogQuill/runtimes/$rid/native"
cp "$built" "$scratch/dotnet/src/LogQuill/runtimes/$rid/native/"
dotnet pack "$scratch/dotnet/src/LogQuill/LogQuill.csproj" -c Release -o "$scratch/feed" >/dev/null

package=$(ls "$scratch/feed"/LogQuill.*.nupkg)
contents=$(unzip -Z1 "$package")
echo "$contents" | grep -q "runtimes/$rid/native/$lib" || { echo "pack-smoke: the package does not carry runtimes/$rid/native/$lib" >&2; exit 1; }
echo "$contents" | grep -q "lib/net8.0/LogQuill.dll" || { echo "pack-smoke: the package has no net8.0 assembly" >&2; exit 1; }
echo "$contents" | grep -q "lib/net10.0/LogQuill.dll" || { echo "pack-smoke: the package has no net10.0 assembly" >&2; exit 1; }
stray=$(echo "$contents" | grep -vE '^(lib/|runtimes/|LogQuill\.nuspec$|README\.md$|\[Content_Types\]\.xml$|_rels/|package/|.*\.psmdcp$)' || true)
[ -z "$stray" ] || { echo "pack-smoke: files that should not ship: $stray" >&2; exit 1; }

# a new console app that installs it from the local feed
app="$scratch/app"
mkdir -p "$app"
cat > "$app/app.csproj" <<EOF
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net10.0</TargetFramework><Nullable>enable</Nullable><ImplicitUsings>enable</ImplicitUsings></PropertyGroup>
  <ItemGroup><PackageReference Include="LogQuill" Version="3.0.0-rc.2" /></ItemGroup>
</Project>
EOF
cat > "$app/NuGet.config" <<EOF
<configuration><packageSources><clear /><add key="local" value="$scratch/feed" /></packageSources></configuration>
EOF
cat > "$app/Program.cs" <<'EOF'
using LogQuill;
using var log = new Logger(new LoggerOptions { Plugins = { PluginConfig.Redact() } });
var record = log.LogRecord(Level.Info, "hello", new { Password = "p", Keep = 1 });
Console.WriteLine(record!.Contains("\"password\":\"***\"") && record.Contains("\"keep\":1") ? "ok " + Core.CanonicalJson("{\"b\":1,\"a\":2}") : "WRONG " + record);
EOF
out=$(cd "$app" && env -u LOGQUILL_LIB dotnet run -c Release 2>&1) || { echo "$out" >&2; echo "pack-smoke: the app failed" >&2; exit 1; }
case "$out" in
  'ok {"a": 2, "b": 1}') ;;
  *) echo "pack-smoke: the app printed: $out" >&2; exit 1 ;;
esac

echo "pack-smoke ok: $(basename "$package") ($(du -k "$package" | cut -f1) KiB, $rid) installed from a local feed into a new app and used by name"
