# Package a Python distribution from the pinned source of its flake.
{
  source,
  dependencies ? (_: [ ]),
  imports ? [ ],
}:
pythonPackages:
let
  inherit (pythonPackages) buildPythonPackage hatchling;
  inherit (pythonPackages.pkgs.lib.fileset)
    unions
    toSource
    fileFilter
    maybeMissing
    ;
  projectFile = source + "/pyproject.toml";
  project = (builtins.fromTOML (builtins.readFile projectFile)).project;
in
buildPythonPackage {
  pname = project.name;
  inherit (project) version;
  pyproject = true;
  src = toSource {
    root = source;
    fileset = unions [
      projectFile
      (maybeMissing (source + "/README.md"))
      (fileFilter (file: file.hasExt "py" || file.name == "py.typed") (source + "/src"))
    ];
  };
  build-system = [ hatchling ];
  dependencies = dependencies pythonPackages;
  pythonImportsCheck = imports;
}
