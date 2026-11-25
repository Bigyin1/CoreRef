# Expose the pinned CLI and install its dependency wheels into an optional venv.
{
  pkgs,
  flowPackage,
  cli,
}:
let
  pythonModules = pkgs.python3Packages.requiredPythonModules [ flowPackage ];
  wheels = pkgs.lib.concatMapStringsSep " " (package: "${package.dist}/*.whl") (
    builtins.filter (package: package ? dist) pythonModules
  );
  venv = pkgs.writeShellApplication {
    name = "coreref-venv";
    runtimeInputs = [ pkgs.uv ];
    text = ''
      destination="''${1:-.venv}"
      uv venv --allow-existing --python ${pkgs.python3}/bin/python3 "$destination"
      nix-store --realise --add-root "$destination/.nix-python" ${pkgs.python3} >/dev/null
      uv pip install --offline --no-index --reinstall --python "$destination/bin/python" ${wheels}
    '';
  };
in
{
  flow = {
    type = "app";
    program = "${flowPackage}/bin/${cli}";
  };
  venv = {
    type = "app";
    program = "${venv}/bin/coreref-venv";
  };
}
