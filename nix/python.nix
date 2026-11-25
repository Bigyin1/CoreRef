# Package the CoreRef SDK and stage runner from the flake's source.
pythonPackages:
let
  inherit (pythonPackages) buildPythonPackage hatchling;
  inherit (pythonPackages.pkgs.lib.fileset) unions toSource;
  project = builtins.fromTOML (builtins.readFile ../pyproject.toml);
in
buildPythonPackage {
  pname = project.project.name;
  inherit (project.project) version;
  pyproject = true;
  src = toSource {
    root = ../.;
    fileset = unions [
      ../README.md
      ../pyproject.toml
      ../src
    ];
  };
  build-system = [ hatchling ];
  pythonImportsCheck = [ "coreref" ];
}
