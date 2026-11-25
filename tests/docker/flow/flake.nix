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
      stageMap = ./src/coreref_flow/stage-map.toml;
      cli = "coreref-flow";
      pythonDependencies = p: [ p.typer ];
      configure = pkgs: {
        tools = {
          yosys = [ pkgs.yosys ];
          openroad = [ pkgs.openroad ];
          tcl = [ pkgs.tcl ];
          verilator = [
            verilatorPkgs.legacyPackages.${pkgs.stdenv.hostPlatform.system}.verilator
            pkgs.stdenv.cc
            pkgs.gnumake
          ];
        };
        parameters.platform = pkgs.fetchgit {
          name = "orfs-nangate45";
          url = "https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts.git";
          rev = "a9adcf481395d3b1355afda40df1c8bde10a0e40";
          rootDir = "flow/platforms/nangate45";
          fetchSubmodules = false;
          hash = "sha256-zGNtgSAEz96TXxUrmGQxM+7cXoIcihs2K5RT5mJ777U=";
        };
      };
    };
}
