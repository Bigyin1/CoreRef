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
    let
      forSystems = nixpkgs.lib.genAttrs [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
      flowFor =
        system:
        import ./nix {
          inherit system;
          pkgs = nixpkgs.legacyPackages.${system};
          core = coreref.lib;
          inherit (verilatorPkgs.legacyPackages.${system}) verilator;
        };
      flows = forSystems flowFor;
      apps = nixpkgs.lib.mapAttrs (_: flow: flow.apps) flows;
      devShells = nixpkgs.lib.mapAttrs (_: flow: { default = flow.devShell; }) flows;
      projectOutputs =
        withVariants: build:
        let
          projects = nixpkgs.lib.mapAttrs (_: build) flows;
        in
        {
          inherit apps devShells;
          packages = nixpkgs.lib.mapAttrs (_: project: project.packages) projects;
          lib = {
            componentFor = system: projects.${system}.component;
          }
          // nixpkgs.lib.optionalAttrs withVariants {
            mkVariant = { system, ... }@args: projects.${system}.variant (removeAttrs args [ "system" ]);
          };
        };
    in
    {
      inherit apps devShells;
      lib = {
        mkProject = config: projectOutputs false (flow: flow.mkProject config);
        mkMemory = config: projectOutputs true (flow: flow.mkMemory config);
      };
      packages = nixpkgs.lib.mapAttrs (_: flow: { default = flow.flowPackage; }) flows;
    };
}
