#!/bin/sh
# Builds the library, regenerates the header and fails if it differs from the committed one, compiles tests/c/smoke.c
# against it with the system C compiler, and runs it. Usage: crates/logquill-ffi/tests/c/run.sh   (from anywhere)
set -eu

here=$(cd "$(dirname "$0")" && pwd)
crate=$(cd "$here/../.." && pwd)
root=$(cd "$crate/../.." && pwd)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

cargo build -p logquill-ffi --manifest-path "$root/Cargo.toml"

# the committed header must be what cbindgen generates now
cbindgen --config "$crate/cbindgen.toml" --crate logquill-ffi --output "$scratch/logquill.h" "$crate"
if ! diff -u "$crate/include/logquill.h" "$scratch/logquill.h"; then
  echo "include/logquill.h is out of date: run cbindgen --config cbindgen.toml --crate logquill-ffi --output include/logquill.h" >&2
  exit 1
fi

lib="$root/target/debug"
cc -Wall -Wextra -Werror -I "$crate/include" "$here/smoke.c" -L "$lib" -llogquill_ffi -o "$scratch/smoke"
DYLD_LIBRARY_PATH="$lib" LD_LIBRARY_PATH="$lib" "$scratch/smoke" "$scratch/smoke.log"
