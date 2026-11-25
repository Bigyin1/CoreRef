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
"${build[@]}" "$increment#simulate" "$baseline#simulate" "$baseline#simulate-dft" "$baseline#lint-physical"

rtl=$(output_path "$baseline#rtl")/hdl
dft=$(output_path "$baseline#dft")/hdl
lint_report=$(output_path "$baseline#lint-physical")/inputs.json
"${build[@]}" "$baseline#rtl" "$baseline#dft"
test "$(grep -rF 'module increment(' "$rtl" | wc -l)" -eq 1
grep -rF 'module sram_4x4' "$rtl"
grep -rF 'module sram_8x8' "$rtl"
grep -rF 'module counter' "$rtl"
test "$(grep -rF 'module increment(' "$dft" | wc -l)" -eq 1
grep -rF 'define DEMO_SCAN' "$dft"
if grep -qrE 'module (counter|sram_[48]x[48])' "$dft"; then
    printf 'DFT output includes the physical macro RTL.\n' >&2
    exit 1
fi
grep -F 'PASS increment' "$(output_path "$increment#simulate")/simulation.log"
grep -F 'PASS SoC' "$(output_path "$baseline#simulate")/simulation.log"
grep -F 'PASS SoC' "$(output_path "$baseline#simulate-dft")/simulation.log"
grep -F '/increment.v' "$lint_report"
grep -F 'macros/counter/abstract.lef' "$lint_report"
grep -F 'macros/counter/timing.lib' "$lint_report"
for size in 4x4 8x8; do
    grep -F "macros/sram-$size/abstract.lef" "$lint_report"
    grep -F "macros/sram-$size/timing.lib" "$lint_report"
done
if grep -qF -- '-counter/' "$lint_report"; then
    printf 'Physical lint received the macro RTL.\n' >&2
    exit 1
fi

# The physical route exports each reusable view as a separate Nix output.
for view in lef liberty spef; do
    test "$(nix eval --no-update-lock-file --raw "$baseline#$view.outputName")" = "$view"
done

artifacts=(
    "$flow#default"
    "$baseline#dft" "$baseline#netlist" "$baseline#floorplan"
    "$baseline#check-netlist" "$baseline#lef" "$baseline#liberty" "$baseline#spef" "$baseline#gds"
    "$counter#lef" "$counter#liberty" "$counter#gds"
    "$status_register#dft" "$sram#4x4" "$sram#8x8"
)
for size in 4x4 8x8; do
    artifacts+=("$sram#$size-lef" "$sram#$size-liberty" "$sram#$size-gds")
done
"${build[@]}" "${artifacts[@]}"
nix key generate-secret --key-name coreref-test-1 > "$repos/cache.sec"
nix key convert-secret-to-public < "$repos/cache.sec" > /cache/cache-key.pub
nix copy --no-update-lock-file --to "file:///cache?secret-key=$repos/cache.sec" "${artifacts[@]}"
printf '%s\n' "$baseline" "$changed" "$(fixture_ref coreref)" "$flow" > /cache/refs
