# Run one Python transform. Nix keeps the identities of its exact inputs and outputs.
{
  pkgs,
  python,
  name,
  transform,
  inputs,
  outputs,
  configuration,
  packages,
  orderOnly,
  licensed,
}:
let
  inherit (pkgs) lib;
  inherit (builtins)
    attrNames
    attrValues
    length
    match
    toJSON
    ;
  validFile =
    path:
    match "[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*" path != null
    && lib.all (part: part != "." && part != "..") (lib.splitString "/" path);
  validation =
    if !(outputs ? out) then
      throw "${name}: declare outputs.out"
    else if !(lib.all validFile (attrNames inputs ++ attrValues outputs)) then
      throw "${name}: stage input and output filenames must be relative"
    else if length (lib.unique (attrValues outputs)) != length (attrValues outputs) then
      throw "${name}: two outputs refer to the same file"
    else
      true;

  toolPin =
    tool:
    if
      match "[A-Za-z0-9][A-Za-z0-9_.-]*" tool.profile != null
      && match "[A-Za-z0-9_.+-]*[0-9][A-Za-z0-9_.+-]*" tool.version != null
      && match "[A-Za-z0-9_.+-]+(/[A-Za-z0-9_.+-]+)+" tool.module != null
      && lib.hasSuffix ("/" + tool.version) tool.module
      && match "[A-Za-z0-9_.+-]+" tool.executable != null
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
      throw "${name}: site tools require an exact version and a versioned module";

  pins = map toolPin licensed;
  profiles = lib.unique (map (tool: tool.profile) pins);
  profile =
    if length profiles == 1 then
      builtins.head profiles
    else
      throw "${name}: site tools for one stage must use the same installation profile";
  setup = lib.optionalString (pins != [ ]) ''
    source /etc/coreref/eda/${profile}/setup.sh
    module load ${lib.escapeShellArgs (map (tool: tool.module) pins)}
  '';
  attrs = {
    nativeBuildInputs = [ python ] ++ packages;
    outputs = [ "out" ] ++ builtins.filter (output: output != "out") (attrNames outputs);
    passAsFile = [ "stagePlan" ];
    stagePlan = toJSON {
      inherit
        name
        transform
        inputs
        outputs
        configuration
        orderOnly
        pins
        ;
    };
  }
  // lib.optionalAttrs (pins != [ ]) {
    __noChroot = true;
    requiredSystemFeatures = [ "coreref-eda-${profile}" ];
  };
in
assert validation;
pkgs.runCommand name attrs ''
  ${setup}
  exec ${python}/bin/python -m coreref._internal.nix_builder
''
