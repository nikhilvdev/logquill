#!/bin/sh
# Builds core.wasm: crates/logquill-ffi for wasm32-wasip1 without the runtime feature, copied next to the Go sources where
# `//go:embed` finds it. A tagged release must carry this file in the tree (the Go module proxy builds from the tag), so the
# release flow commits it; day to day it is untracked.
#   bindings/go/scripts/build-core.sh
set -eu
here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
rustup target add wasm32-wasip1 >/dev/null 2>&1 || true
cargo build --manifest-path "$repo/Cargo.toml" -p logquill-ffi --release --target wasm32-wasip1 --no-default-features
cp "$repo/target/wasm32-wasip1/release/logquill_ffi.wasm" "$here/../core.wasm"
echo "wrote bindings/go/core.wasm ($(wc -c < "$here/../core.wasm") bytes)"
