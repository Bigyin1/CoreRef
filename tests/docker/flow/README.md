# CoreRef Flow fixture

The RTL, hierarchical DFT and OpenROAD stages are used by the CoreRef Docker integration tests.

The verification stages turn the ordered RTL source list and defines into EDAM
and use Edalize to configure and run Verilator. Nix selects and provides the
Verilator executable and caches the declared lint and simulation outputs.
Edalize's release archive is a flake input pinned in `flake.lock`.
Nix adds Edalize to the Verilator stage environments. The `verification` Python
extra provides it for running these transforms directly during development.
