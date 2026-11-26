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
      mkStage = import ./nix/mk-stage.nix;
      forSystems =
        f:
        nixpkgs.lib.genAttrs systems (
          system:
          let
            pkgs = nixpkgs.legacyPackages.${system};
          in
          f {
            inherit pkgs mkStage;
            coreref = import ./nix/python.nix { inherit pkgs; };
          }
        );
    in
    {
      lib = { inherit mkStage; };
      packages = forSystems (
        { pkgs, coreref, ... }@args:
        {
          default = coreref;
        }
        // pkgs.lib.optionalAttrs pkgs.stdenv.hostPlatform.isLinux (import ./tests/nix/licensed.nix args)
      );
      checks = forSystems (import ./tests/nix);
    };
}
