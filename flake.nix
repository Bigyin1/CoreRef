{
  description = "CoreRef framework for native Nix stages implemented in Python";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/94c3211b0ee8ba969a3f12eb4aff8ba0f72971dd";

  outputs =
    { nixpkgs, ... }:
    let
      systems = [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
      forSystems = nixpkgs.lib.genAttrs systems;
      core = rec {
        mkRoute = import ./nix/route.nix;
        pythonPackage = import ./nix/python.nix {
          source = ./.;
          imports = [ "coreref" ];
        };
        mkFlow = import ./nix/flow.nix { inherit systems mkRoute pythonPackage; };
      };
    in
    {
      lib = core;
      packages = forSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = core.pythonPackage pkgs.python3Packages;
        }
        // pkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux (
          import ./tests/nix/licensed.nix { inherit pkgs core; }
        )
      );
      checks = forSystems (
        system:
        import ./tests/nix {
          inherit core;
          pkgs = nixpkgs.legacyPackages.${system};
        }
      );
    };
}
