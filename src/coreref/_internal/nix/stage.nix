# Bind the Python plan to native derivation dependencies and named outputs.
{ plan, pythonRoot }:
let
  python = if pythonRoot == "" then import ./runtime.nix else builtins.storePath pythonRoot;
  storePrefix = "${builtins.storeDir}/";
  input =
    _: file:
    if builtins.isAttrs file then
      "${stages.${file.stage}.${file.output}}/${file.filename}"
    else if builtins.substring 0 (builtins.stringLength storePrefix) file == storePrefix then
      builtins.storePath file
    else
      builtins.path {
        path = /. + file;
        name = "coreref-input";
      };
  stages = builtins.mapAttrs (
    _: stage:
    derivation {
      inherit (stage) name;
      system = builtins.currentSystem;
      builder = "${python}/bin/python";
      args = [ ../nix_builder.py ];
      PATH = "${python}/bin";
      outputs = [ "out" ] ++ builtins.filter (name: name != "out") (builtins.attrNames stage.outputs);
      __noChroot = stage.execution == "relaxed";
      passAsFile = [ "stagePlan" ];
      stagePlan = builtins.toJSON (
        stage
        // {
          inputs = builtins.mapAttrs input stage.inputs;
          modules = builtins.mapAttrs (_: builtins.storePath) stage.modules;
          needs = map (name: "${stages.${name}.out}") stage.needs;
        }
      );
    }
  ) plan;
in
stages
