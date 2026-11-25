{
  description = "CoreRef RTL, hierarchical DFT and OpenROAD flow";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/94c3211b0ee8ba969a3f12eb4aff8ba0f72971dd";
    # This revision carries the Verilator build that also works on Darwin.
    verilatorPkgs.url = "github:NixOS/nixpkgs/73e728ddb6b7a12d18808f510813a13ee1fe4cce";
    coreref = {
      url = "git+http://gitlab:18081/root/coreref.git?ref=fixture";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      nixpkgs,
      verilatorPkgs,
      coreref,
      ...
    }:
    coreref.lib.mkFlow {
      inherit nixpkgs;
      source = ./.;
      project = import ./nix/project.nix {
        inherit verilatorPkgs;
        inherit (coreref.lib) selectComponents;
      };
      cli = "coreref-flow";
      pythonDependencies = p: [ p.typer ];
    };
}
