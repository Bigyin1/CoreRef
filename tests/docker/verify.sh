#!/usr/bin/env bash
# Run in a separate container with its own Nix store.
set -euo pipefail
source /suite/common.sh

cache=http://cache
refs=$(curl -fsS "$cache/refs")
mapfile -t scenario <<< "$refs"
test "${#scenario[@]}" -eq 4
baseline=${scenario[0]}
changed=${scenario[1]}
core=${scenario[2]}
flow=${scenario[3]}
public_key=$(curl -fsS "$cache/cache-key.pub")
fetch=(
    nix build --no-update-lock-file --no-link --max-jobs 0 --builders ''
    --option substituters "$cache" --option extra-trusted-public-keys "$public_key"
    --option require-sigs true
)
gds=$(output_path "$baseline#gds")
lef=$(output_path "$baseline#lef")
test ! -e "$gds"
test ! -e "$lef"
"${fetch[@]}" "$baseline#lef"
test -s "$lef/abstract.lef"
test ! -e "$gds"

bash /suite/checks/placement-cache.sh "$baseline" "$changed" "$cache" "$public_key"
"${fetch[@]}" "$baseline#gds" "$flow#default"
header=$(od -An -tx1 -N4 "$gds/final.gds" | tr -d '[:space:]')
test "$header" = 00060002
test -x "$(output_path "$flow#default")/bin/coreref-flow"

bash /suite/checks/licensed-worker.sh "$core" "$cache/nix-cache-info"
