# Bind a stage map and a Python distribution to a set of flake outputs.
{
  systems,
  mkRoute,
  pythonPackage,
}:
{
  nixpkgs,
  source,
  stageMap,
  configure ? (_: { }),
  pythonDependencies ? (_: [ ]),
  cli ? null,
}:
let
  inherit (nixpkgs) lib;
  recipe = builtins.fromTOML (builtins.readFile stageMap);
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
      environment = configure pkgs;
      python = names: pkgs.python3.withPackages (p: [ (flowPython p) ] ++ map (name: p.${name}) names);
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
      devShell = pkgs.mkShell { packages = [ (python [ ]) ]; };
      project = import ./project.nix {
        inherit lib recipe system;
        flowSource = source;
        defaults = environment.parameters or { };
        route =
          config:
          mkRoute (
            {
              inherit pkgs python stageMap;
              tools = environment.tools or { };
            }
            // config
          );
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
      profile = recipe.profiles.${config.profile} or (throw "unknown route profile '${config.profile}'");
      projects = lib.mapAttrs (_: flow: flow.project profile config) outputs;
    in
    {
      inherit apps devShells;
      packages = lib.mapAttrs (_: project: project.packages) projects;
      lib.componentFor = system: projects.${system}.component;
    };
}
