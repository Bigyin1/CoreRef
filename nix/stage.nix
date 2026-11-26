# Run one Python transform. Nix keeps the identities of its exact inputs and outputs.
{
  pkgs,
  python,
  plan,
  packages,
  licensed,
}:
let
  inherit (pkgs) lib;
  inherit (plan) name outputs;
  inherit (builtins)
    attrNames
    attrValues
    length
    toJSON
    ;
  validPart =
    part:
    !(builtins.elem part [
      ""
      "."
      ".."
    ]);
  validFile = path: lib.all validPart (lib.splitString "/" path);
  validName = value: validPart value && !(lib.hasInfix "/" value);
  nonEmptyToken =
    value:
    builtins.isString value
    && value != ""
    && !(lib.any (separator: lib.hasInfix separator value) [
      " "
      "\t"
      "\n"
      "\r"
    ]);
  validPlan =
    if !(outputs ? out) then
      throw "${name}: declare outputs.out"
    else if !(lib.all validFile (attrNames plan.inputs ++ attrValues outputs)) then
      throw "${name}: stage input and output paths must be relative"
    else if length (lib.unique (attrValues outputs)) != length (attrValues outputs) then
      throw "${name}: two outputs refer to the same path"
    else
      true;

  toolPin =
    tool:
    if
      nonEmptyToken tool.profile
      && validName tool.profile
      && nonEmptyToken tool.version
      && validFile tool.module
      && !(lib.hasPrefix "-" tool.module)
      && validName tool.executable
    then
      {
        inherit (tool)
          profile
          executable
          module
          version
          ;
        versionArgs = tool.versionArgs or [ "--version" ];
      }
    else
      throw "${name}: invalid site tool profile, version, module or executable";

  pins = map toolPin licensed;
  profiles = lib.unique (map (tool: tool.profile) pins);
  profile =
    if length profiles == 1 then
      builtins.head profiles
    else
      throw "${name}: site tools for one stage must use the same installation profile";
  setup = lib.optionalString (pins != [ ]) ''
    source ${lib.escapeShellArg "/etc/coreref/eda/${profile}/setup.sh"}
    module load ${lib.escapeShellArgs (map (tool: tool.module) pins)}
  '';
  attrs = {
    nativeBuildInputs = [ python ] ++ packages;
    outputs = [ "out" ] ++ builtins.filter (output: output != "out") (attrNames outputs);
    passAsFile = [ "stagePlan" ];
    stagePlan = toJSON (plan // { inherit pins; });
  }
  // lib.optionalAttrs (pins != [ ]) {
    __noChroot = true;
    requiredSystemFeatures = [ "coreref-eda-${profile}" ];
  };
in
assert validPlan;
pkgs.runCommand name attrs ''
  ${setup}
  exec ${python}/bin/python -m coreref._internal.nix_builder
''
