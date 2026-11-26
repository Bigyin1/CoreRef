# CoreRef Flow fixture

The RTL, hierarchical DFT and OpenROAD stages are used by the CoreRef Docker integration tests.

The flake exports packages, an app and a developer shell. Its project function uses
`coreref.lib.mkStage` directly. The stage runtime includes the transform modules and
their resources. The CLI and Typer are built separately.

From a project shell, use the CLI to discover and build targets, inspect build
logs and work with dependencies.

```sh
coreref-flow targets
coreref-flow simulate
coreref-flow log simulate
coreref-flow deps
coreref-flow workspace checkout counter
```

The flow selects component views in dependency order. Behavioral and physical
targets share traversal rules, including deduplication of shared RTL and stopping
at hard macros. Each target selects the relevant views, such as behavioral RTL
or LEF and Liberty for physical lint.

The verification stages turn the ordered RTL source list and defines into EDAM
and use Edalize to configure and run Verilator. Nix selects and provides the
Verilator executable and caches the declared lint and simulation outputs.
Edalize's release archive is a flake input pinned in `flake.lock`.
Nix adds Edalize to the Verilator stage environments. The `verification` Python
extra provides it for running these transforms directly during development.
