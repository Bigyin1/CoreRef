#!/usr/bin/env bash
# Exercise the public SDK through the fixture Flow CLI.
set -euo pipefail
cd "$1"
baseline=$2
cli=(nix run --no-update-lock-file "$baseline#flow" --)
lock=$(mktemp)
cp flake.lock "$lock"

nix develop --no-update-lock-file "$baseline" --command bash -ec '
    python -c "from coreref import Project; from coreref_flow.cli import app"
    coreref-flow deps
' | grep -F $'increment\t'

"${cli[@]}" workspace checkout increment .coreref/increment
cp /suite/variants/increment.v .coreref/increment/rtl/increment.v
rtl=$("${cli[@]}" build rtl . --no-link)
grep -rF '// Workspace override' "$rtl/hdl"
rtl_url=$("${cli[@]}" build rtl "path:$PWD" --no-link)
grep -rF '// Workspace override' "$rtl_url/hdl"
cmp "$lock" flake.lock
"${cli[@]}" workspace remove increment
locked_rtl=$("${cli[@]}" build rtl "path:$PWD" --no-link)
test "$locked_rtl" != "$rtl_url"
cmp "$lock" flake.lock

# The mounted source has a DFT readiness marker. Remove it from the test checkout.
rm dft/enabled
if nix eval --no-update-lock-file --raw .#dft.drvPath >/dev/null 2>&1; then
    printf 'DFT is still available after removing its readiness marker.\n' >&2
    exit 1
fi
nix eval --no-update-lock-file --raw .#synth.stagePlan |
    grep -F '"name":"soc-rtl-synthesis-input"'
netlist=$(nix build --no-update-lock-file --no-link --print-out-paths .#netlist)
grep -F 'module soc' "$netlist/netlist.v"
cmp "$lock" flake.lock
