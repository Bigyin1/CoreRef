#!/usr/bin/env bash
# Exercise the CoreRef licensed-stage fixture with Environment Modules and live HTTP.
set -euo pipefail

if (( $# != 2 )); then
    printf 'Usage: %s COREREF_FLAKE NIX_CACHE_INFO_URL\n' "$0" >&2
    exit 2
fi

stage="$1#licensed-test"
feature=coreref-eda-test-v1
profile=/etc/coreref/eda/test-v1

fail() {
    printf 'Licensed worker check failed: %s\n' "$1" >&2
    exit 1
}

test -x /usr/bin/modulecmd
test -x /usr/bin/curl
output=$(nix eval --no-update-lock-file --raw "$stage.outPath")
[[ ! -e "$output" ]] || fail "worker already contains $output"
[[ "$(nix eval --no-update-lock-file --json "$stage.requiredSystemFeatures")" == '["coreref-eda-test-v1"]' ]] \
    || fail 'licensed stage lacks the worker feature'
[[ "$(nix eval --no-update-lock-file --json "$stage.__noChroot")" == true ]] \
    || fail 'licensed stage lacks its sandbox exception'

mkdir -p /etc/coreref/eda
[[ ! -e "$profile" ]] || fail "worker already has the test profile"
cp -R /suite/site-tools/test-v1 "$profile"
trap 'rm -rf -- "$profile"' EXIT
printf '%s\n' "$2" > "$profile/endpoint"

build=(nix build --no-update-lock-file --no-link --builders '' --print-out-paths -L)
printf '1.2-hf3\n' > "$profile/version"
if "${build[@]}" --option system-features '' "$stage" >"$profile/build.log" 2>&1; then
    fail 'stage accepted a worker without its feature'
fi
grep -F "$feature" "$profile/build.log" || { cat "$profile/build.log" >&2; exit 1; }
[[ ! -e "$profile/tool-ran" ]] || fail 'tool ran without its worker feature'

printf '1.2-hf30\n' > "$profile/version"
if "${build[@]}" --option extra-system-features "$feature" "$stage" >"$profile/build.log" 2>&1; then
    fail 'stage accepted the wrong tool version'
fi
grep -F 'did not report required version 1.2-hf3' "$profile/build.log" \
    || { cat "$profile/build.log" >&2; exit 1; }
[[ ! -e "$profile/tool-ran" ]] || fail 'tool ran with the wrong version'

printf '1.2-hf3\n' > "$profile/version"
result=$(
    "${build[@]}" --option sandbox relaxed --option sandbox-fallback false \
        --option extra-system-features "$feature" "$stage"
)
test -f "$profile/tool-ran"
grep -Fx 'StoreDir: /nix/store' "$result/result.txt"
printf 'Licensed fixture loaded example/1.2, verified 1.2-hf3 and reached the HTTP endpoint.\n'
