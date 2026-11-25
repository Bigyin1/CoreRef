#!/usr/bin/env bash
# Compare two pinned SoC revisions and verify the builds performed by a clean worker.
set -euo pipefail

if (( $# != 4 )); then
    printf 'Usage: %s BASE_FLAKE CHANGED_FLAKE CACHE_URL CACHE_PUBLIC_KEY\n' "$0" >&2
    exit 2
fi
base=$1
changed=$2
cache=$3
public_key=$4

fail() {
    printf 'Placement check failed: %s\n' "$1" >&2
    exit 1
}

target_attr() {
    nix eval --no-update-lock-file --raw "$1#$2.$3"
}

unchanged_stages=(rtl dft netlist check-netlist floorplan)
changed_stages=(place cts route gds)
declare -A recipes

for target in "${unchanged_stages[@]}"; do
    before=$(target_attr "$base" "$target" drvPath)
    recipes[$target]=$(target_attr "$changed" "$target" drvPath)
    [[ "$before" == "${recipes[$target]}" ]] || fail "$target derivation changed"
done
for target in "${changed_stages[@]}"; do
    before=$(target_attr "$base" "$target" drvPath)
    recipes[$target]=$(target_attr "$changed" "$target" drvPath)
    [[ "$before" != "${recipes[$target]}" ]] || fail "$target derivation stayed the same"
done

netlist=$(target_attr "$changed" netlist outPath)
floorplan=$(target_attr "$changed" floorplan outPath)
gds=$(target_attr "$changed" gds outPath)
for output in "$netlist" "$floorplan" "$gds"; do
    [[ ! -e "$output" ]] || fail "worker already contains $output"
done

build=(nix build --no-update-lock-file --no-link --log-format raw --builders '')
trust=(--option extra-trusted-public-keys "$public_key" --option require-sigs true)

# Requiring substitution from this cache proves the hits before we enable local builds.
"${build[@]}" "${trust[@]}" --max-jobs 0 --option substituters "$cache" \
    "$changed#netlist" "$changed#floorplan"
grep -q '^module soc' "$netlist/netlist.v"
test -s "$floorplan/floorplan.odb"

build_log=$(mktemp)
trap 'rm -f "$build_log"' EXIT
if ! "${build[@]}" "${trust[@]}" --option extra-substituters "$cache" "$changed#gds" 2>"$build_log"; then
    cat "$build_log" >&2
    fail 'GDS build failed'
fi
for target in "${unchanged_stages[@]}"; do
    if grep -qF "building '${recipes[$target]}'" "$build_log"; then
        fail "$target was rebuilt"
    fi
done
for target in "${changed_stages[@]}"; do
    grep -F "building '${recipes[$target]}'" "$build_log" || fail "$target was not built"
done

header=$(od -An -tx1 -N4 "$gds/final.gds" | tr -d '[:space:]')
[[ "$header" == 00060002 ]] || fail 'invalid GDS header'
printf 'Reused signed netlist and floorplan. Rebuilt place, CTS, route and GDS.\n'
