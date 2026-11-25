# Expose the pinned CLI and an optional venv built from the same wheels.
{
  pkgs,
  flowPackage,
  sdkPackage,
}:
let
  venv = pkgs.writeShellApplication {
    name = "coreref-venv";
    runtimeInputs = [ pkgs.uv ];
    text = ''
      destination="''${1:-.venv}"
      uv venv --allow-existing --python ${pkgs.python3}/bin/python3 "$destination"
      nix-store --realise --add-root "$destination/.nix-python" ${pkgs.python3} >/dev/null
      uv pip install --offline --no-index --reinstall --python "$destination/bin/python" \
        ${sdkPackage.dist}/*.whl ${flowPackage.dist}/*.whl
    '';
  };
in
{
  apps = {
    flow = {
      type = "app";
      program = "${flowPackage}/bin/coreref-flow";
    };
    venv = {
      type = "app";
      program = "${venv}/bin/coreref-venv";
    };
  };
}
