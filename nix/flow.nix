# Package a Python Flow and expose the projects it defines.
{
  systems,
  mkStage,
  pythonPackage,
}:
{
  nixpkgs,
  source,
  project,
  pythonDependencies ? (_: [ ]),
  cli ? null,
}:
let
  inherit (nixpkgs) lib;
  metadata = (builtins.fromTOML (builtins.readFile (source + "/pyproject.toml"))).project;
  flowPython = import ./python.nix {
    inherit source;
    dependencies = p: [ (pythonPackage p) ] ++ pythonDependencies p;
    imports = lib.optional (cli != null) (builtins.head (lib.splitString ":" metadata.scripts.${cli}));
  };

  forSystems = lib.genAttrs systems;
  outputs = forSystems (
    system:
    let
      pkgs = nixpkgs.legacyPackages.${system};
      python = packages: pkgs.python3.withPackages (p: [ (flowPython p) ] ++ packages p);
      package = flowPython pkgs.python3Packages;
    in
    {
      inherit package;
      apps = lib.optionalAttrs (cli != null) {
        flow = {
          type = "app";
          program = "${package}/bin/${cli}";
        };
      };
      devShell = pkgs.mkShell { packages = [ (python (_: [ ])) ]; };
      mkProject = project {
        inherit pkgs system;
        mkStage = mkStage { inherit pkgs python; };
      };
    }
  );
  apps = lib.mapAttrs (_: flow: flow.apps) outputs;
  devShells = lib.mapAttrs (_: flow: { default = flow.devShell; }) outputs;
in
{
  inherit apps devShells;
  packages = lib.mapAttrs (_: flow: { default = flow.package; }) outputs;
  lib.mkProject =
    config:
    let
      projects = lib.mapAttrs (_: flow: flow.mkProject config) outputs;
    in
    {
      inherit apps devShells;
      packages = lib.mapAttrs (_: project: project.packages) projects;
      lib.componentFor = system: projects.${system}.component;
    };
}
