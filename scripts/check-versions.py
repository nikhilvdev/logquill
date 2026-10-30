#!/usr/bin/env python3
"""Checks that every manifest carries one lockstep version, and (with a tag) that the tag is that version.

    scripts/check-versions.py            # the manifests agree with each other
    scripts/check-versions.py v3.0.0     # ... and with the tag a release job was started for

The plan's release rule (docs/rust-core-plan.md, section 7) is "the tag equals every manifest". PyPI and RubyGems spell a
pre-release `3.0.0.dev0` where Cargo, npm, NuGet and Gradle spell it `3.0.0-dev.0`; both are the same version here.
Exit status 1 and a list of the mismatches if anything differs.
"""

import json
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent


def first(path: str, pattern: str) -> str:
    text = (root / path).read_text(encoding="utf-8")
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        sys.exit(f"check-versions: no version found in {path} (pattern {pattern!r}); update this script if the manifest changed")
    return match.group(1)


def normal(version: str) -> str:
    """The Cargo/npm spelling of a version: `3.0.0.dev0` becomes `3.0.0-dev.0`, `3.0.0rc1` and `3.0.0.rc1` become `3.0.0-rc.1`."""
    return re.sub(r"\.?(dev|rc)(\d+)$", r"-\1.\2", version)


manifests = {
    "Cargo.toml (workspace)": first("Cargo.toml", r'^version = "([^"]+)"'),
    "bindings/node/Cargo.toml": first("bindings/node/Cargo.toml", r'^version = "([^"]+)"'),
    "bindings/python/Cargo.toml": first("bindings/python/Cargo.toml", r'^version = "([^"]+)"'),
    "bindings/wasm/Cargo.toml": first("bindings/wasm/Cargo.toml", r'^version = "([^"]+)"'),
    "bindings/python/pyproject.toml": first("bindings/python/pyproject.toml", r'^version = "([^"]+)"'),
    "bindings/node/package.json": json.loads((root / "bindings/node/package.json").read_text(encoding="utf-8"))["version"],
    "bindings/wasm/package.json": json.loads((root / "bindings/wasm/package.json").read_text(encoding="utf-8"))["version"],
    "bindings/ruby/lib/logquill/version.rb": first("bindings/ruby/lib/logquill/version.rb", r'VERSION = "([^"]+)"'),
    "bindings/dotnet/src/LogQuill/LogQuill.csproj": first("bindings/dotnet/src/LogQuill/LogQuill.csproj", r"<Version>([^<]+)</Version>"),
    "bindings/jvm/build.gradle.kts": first("bindings/jvm/build.gradle.kts", r'^version = "([^"]+)"'),
}

expected = normal(sys.argv[1].removeprefix("v")) if len(sys.argv) > 1 else normal(manifests["Cargo.toml (workspace)"])
wrong = {name: version for name, version in manifests.items() if normal(version) != expected}
if wrong:
    print(f"check-versions: expected {expected} everywhere, but:", file=sys.stderr)
    for name, version in wrong.items():
        print(f"  {name} says {version}", file=sys.stderr)
    sys.exit(1)
print(f"check-versions ok: {len(manifests)} manifests at {expected}")
