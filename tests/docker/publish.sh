#!/usr/bin/env bash
# Publish copies of the mounted fixtures, so the verifier can fetch locked Git inputs.
set -euo pipefail
source /suite/common.sh

repos=$(mktemp -d)
git config --global user.name 'CoreRef tests'
git config --global user.email 'tests@example.invalid'
git config --global commit.gpgsign false

commit_fixture() {
    git -C "$1" add --all
    git -C "$1" commit --quiet -m "$2"
}

publish_fixture() {
    local attempt
    printf 'Publishing fixture %s\n' "$1"
    for attempt in 1 2 3 4 5; do
        if git -C "$repos/$1" push --quiet "$gitlab/$1.git" HEAD:refs/heads/fixture >"$repos/push.log" 2>&1; then
            return
        fi
        # Workhorse may return 502 while GitLab finishes its first reconfigure.
        if [[ "$attempt" == 5 ]] || ! grep -qF 'error: 502' "$repos/push.log"; then
            cat "$repos/push.log" >&2
            return 1
        fi
        sleep 5
    done
}

prepare_fixture() {
    local name=$1 source=$2 destination
    destination="$repos/$name"
    cp -R "$source" "$destination"
    nix flake lock "path:$destination"
    git init --quiet --initial-branch fixture "$destination"
    commit_fixture "$destination" 'Import test fixture'
}

# Use the mounted working tree, including edits under test.
sdk="$repos/coreref"
mkdir -p "$sdk/tests"
cp -R /checkout/{.gitignore,flake.nix,flake.lock,pyproject.toml,README.md,nix,src} "$sdk/"
cp -R /checkout/tests/nix "$sdk/tests/"
git init --quiet --initial-branch fixture "$sdk"
commit_fixture "$sdk" 'Import CoreRef under test'
publish_fixture coreref
nix flake check --no-update-lock-file "$(fixture_ref coreref)"

prepare_fixture coreref-flow /suite/flow
publish_fixture coreref-flow
for project in increment sram counter status-register; do
    prepare_fixture "$project" "/suite/projects/$project"
    publish_fixture "$project"
done

prepare_fixture soc /suite/projects/soc
soc="$repos/soc"
baseline="$(fixture_ref soc)&rev=$(git -C "$soc" rev-parse HEAD)"
cp /suite/variants/place.tcl "$soc/be/place.tcl"
commit_fixture "$soc" 'Change only the placement density'
test "$(git -C "$soc" diff HEAD^ HEAD --name-only)" = be/place.tcl
changed="$(fixture_ref soc)&rev=$(git -C "$soc" rev-parse HEAD)"
publish_fixture soc
bash /suite/checks/workspace.sh "$soc" "$baseline"

flow=$(fixture_ref coreref-flow)
counter=$(fixture_ref counter)
status_register=$(fixture_ref status-register)
sram=$(fixture_ref sram)
increment=$(fixture_ref increment)
build=(nix build --no-update-lock-file --no-link)
"${build[@]}" "$increment#simulate" "$baseline#simulate-dft" "$baseline#lint-physical"

rtl=$(output_path "$baseline#rtl")/rtl.v
dft=$(output_path "$baseline#dft")/dft_output.v
lint_report=$(output_path "$baseline#lint-physical")/inputs.json
"${build[@]}" "$baseline#rtl" "$baseline#dft"
test "$(grep -cF 'module increment' "$rtl")" -eq 1
grep -F 'module sram_4x4' "$rtl"
grep -F 'module sram_8x8' "$rtl"
grep -F 'module counter' "$rtl"
test "$(grep -cF 'module increment' "$dft")" -eq 1
grep -F 'define DEMO_SCAN' "$dft"
if grep -qF 'module counter' "$dft"; then
    printf 'DFT output includes the physical macro RTL.\n' >&2
    exit 1
fi
grep -F 'PASS increment' "$(output_path "$increment#simulate")/simulation.log"
grep -F 'PASS SoC' "$(output_path "$baseline#simulate-dft")/simulation.log"
grep -F 'sources/increment.v' "$lint_report"
grep -F 'macros/counter/abstract.lef' "$lint_report"
grep -F 'macros/counter/timing.lib' "$lint_report"
if grep -qF 'sources/counter.v' "$lint_report"; then
    printf 'Physical lint received the macro RTL.\n' >&2
    exit 1
fi

artifacts=(
    "$flow#default"
    "$baseline#rtl" "$baseline#dft" "$baseline#netlist" "$baseline#floorplan"
    "$baseline#check-netlist" "$baseline#lef" "$baseline#liberty" "$baseline#spef" "$baseline#gds"
    "$counter#lef" "$counter#liberty" "$counter#gds"
    "$status_register#dft" "$sram#4x4" "$sram#8x8"
)
"${build[@]}" "${artifacts[@]}"
nix key generate-secret --key-name coreref-test-1 > "$repos/cache.sec"
nix key convert-secret-to-public < "$repos/cache.sec" > /cache/cache-key.pub
nix copy --no-update-lock-file --to "file:///cache?secret-key=$repos/cache.sec" "${artifacts[@]}"
printf '%s\n' "$baseline" "$changed" "$(fixture_ref coreref)" "$flow" > /cache/refs
