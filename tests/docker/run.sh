#!/usr/bin/env bash
set -euo pipefail

suite=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export COREREF_TEST_PASSWORD
COREREF_TEST_PASSWORD=$(od -An -tx1 -N24 /dev/urandom | tr -d '[:space:]')
project="coreref-test-$$"
compose=(docker compose --project-name "$project" -f "$suite/compose.yml")

cleanup() {
    "${compose[@]}" down --volumes --remove-orphans
    docker image rm "$project-publisher" "$project-verifier" >/dev/null 2>&1 || :
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

"${compose[@]}" up -d --wait --wait-timeout 300 gitlab cache
"${compose[@]}" build publisher verifier
"${compose[@]}" run --rm --no-deps -T publisher
"${compose[@]}" run --rm --no-deps -T verifier
