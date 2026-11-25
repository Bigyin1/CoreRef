# Supply the tools and Python package for this particular route.
{
  pkgs,
  core,
  verilator,
  system,
}:
let
  inherit (pkgs) lib;
  flow =
    p:
    let
      project = builtins.fromTOML (builtins.readFile ../pyproject.toml);
    in
    p.buildPythonPackage {
      pname = project.project.name;
      inherit (project.project) version;
      pyproject = true;
      src = lib.fileset.toSource {
        root = ../.;
        fileset = lib.fileset.unions [
          ../README.md
          ../pyproject.toml
          (lib.fileset.fileFilter (file: file.hasExt "py" || file.name == "py.typed") ../src)
        ];
      };
      build-system = [ p.hatchling ];
      dependencies = [ (core.pythonPackage p) ];
      pythonImportsCheck = [ "coreref_flow.cli" ];
    };
  flowPackage = flow pkgs.python3Packages;
  python = names: pkgs.python3.withPackages (p: [ (flow p) ] ++ map (name: p.${name}) names);
  cli = import ./cli.nix {
    inherit pkgs flowPackage;
    sdkPackage = core.pythonPackage pkgs.python3Packages;
  };
  mkRoute =
    config:
    core.mkRoute (
      {
        inherit pkgs;
        stageMap = ../src/coreref_flow/stage-map.toml;
        inherit python;
        tools = {
          yosys = [ pkgs.yosys ];
          openroad = [ pkgs.openroad ];
          verilator = [
            verilator
            pkgs.stdenv.cc
            pkgs.gnumake
          ];
        };
      }
      // config
    );
  # Only materialize the Nangate45 directory when a physical stage needs it.
  platform = pkgs.fetchgit {
    name = "orfs-nangate45";
    url = "https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts.git";
    rev = "a9adcf481395d3b1355afda40df1c8bde10a0e40";
    rootDir = "flow/platforms/nangate45";
    fetchSubmodules = false;
    hash = "sha256-zGNtgSAEz96TXxUrmGQxM+7cXoIcihs2K5RT5mJ777U=";
  };
in
{
  inherit (cli) apps;
  inherit flowPackage;
  devShell = pkgs.mkShell {
    packages = [ (python [ ]) ];
  };
  mkProject = import ./project.nix {
    inherit
      lib
      mkRoute
      platform
      system
      ;
  };
  mkMemory = import ./memory.nix { inherit lib mkRoute; };
}
