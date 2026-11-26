# Package CoreRef for Nix stage runtimes and Python applications.
{ pkgs }:
let
  inherit (pkgs.python3Packages) buildPythonPackage hatchling;
  inherit (pkgs.lib) fileset;
  project = (builtins.fromTOML (builtins.readFile ../pyproject.toml)).project;
in
buildPythonPackage {
  pname = project.name;
  inherit (project) version;
  pyproject = true;
  src = fileset.toSource {
    root = ../.;
    fileset = fileset.unions [
      ../pyproject.toml
      ../README.md
      (fileset.fileFilter (file: file.hasExt "py" || file.name == "py.typed") ../src)
    ];
  };
  build-system = [ hatchling ];
  pythonImportsCheck = [ "coreref" ];
}
