# Build the runner from the same pinned source that supplies the Nix library.
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
