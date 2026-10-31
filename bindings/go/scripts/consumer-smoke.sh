#!/bin/sh
# The gate before publishing: a fresh Go module requires this one by its import path (pointed at this directory with a
# `replace`, since nothing is published) and builds and runs a program that uses it, the way a consumer does. Needs
# core.wasm (scripts/build-core.sh). Everything happens in a scratch directory; nothing is published.
#   bindings/go/scripts/consumer-smoke.sh
set -eu
export PATH="${PATH}:/opt/homebrew/bin"
here=$(cd "$(dirname "$0")" && pwd)
module=$(cd "$here/.." && pwd)
[ -f "$module/core.wasm" ] || { echo "consumer-smoke: $module/core.wasm is missing; run scripts/build-core.sh first" >&2; exit 1; }

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
path=github.com/nikhilvdev/logquill/bindings/go/v3

cd "$scratch"
cat > go.mod <<EOF
module consumer

go 1.24

require $path v3.0.0

replace $path => $module
EOF
cat > main.go <<'EOF'
package main

import (
	"fmt"

	logquill "github.com/nikhilvdev/logquill/bindings/go/v3"
)

func main() {
	log, err := logquill.New(logquill.WithPlugins(logquill.Redact(nil, nil, "")))
	if err != nil {
		panic(err)
	}
	defer log.Close()
	canonical, _ := logquill.CanonicalJSON(`{"b":1,"a":2}`)
	fmt.Println(log.Info("hello", logquill.Meta{"password": "p"}), canonical)
}
EOF
GOFLAGS=-mod=mod go mod tidy >/dev/null 2>&1
out=$(go run . 2>&1) || { echo "$out" >&2; echo "consumer-smoke: the program failed" >&2; exit 1; }
[ "$out" = 'true {"a": 2, "b": 1}' ] || { echo "consumer-smoke: the program printed: $out" >&2; exit 1; }
echo "consumer-smoke ok: a fresh module imported $path and used it"
