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
"${cli[@]}" workspace remove increment
cmp "$lock" flake.lock

# The mounted source has a DFT readiness marker. Remove it from the test checkout.
rm dft/enabled
nix eval --no-update-lock-file --raw .#synth.stagePlan |
    grep -F '"name":"soc-rtl-synthesis-input"'
netlist=$(nix build --no-update-lock-file --no-link --print-out-paths .#netlist)
grep -F 'module soc' "$netlist/netlist.v"
cmp "$lock" flake.lock
