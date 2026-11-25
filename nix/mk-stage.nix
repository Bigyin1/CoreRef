# Bind actual Nix inputs to one Python transform.
{ pkgs, python }:
{
  name,
  transform,
  inputs ? { },
  outputs,
  configuration ? { },
  nativeBuildInputs ? [ ],
  pythonPackages ? (_: [ ]),
  siteTools ? [ ],
  needs ? [ ],
}:
let
  inherit (pkgs) lib;
  isInline = value: builtins.isAttrs value && (value.__corerefInline or false);
  inlineInputs = builtins.filter isInline (builtins.attrValues inputs);

  source =
    path:
    if builtins.isPath path then
      builtins.path {
        inherit path;
        name = baseNameOf path;
      }
    else if (builtins.isString path && builtins.hasContext path) || lib.isDerivation path then
      path
    else
      throw "${name}: stage inputs must be Nix paths or derivation outputs";

  bind =
    alias: value:
    if isInline value then
      [ (lib.nameValuePair alias { inherit (value) plan output; }) ]
    else if builtins.isList value then
      lib.imap0 (
        index: file:
        lib.nameValuePair "${alias}/${lib.fixedWidthNumber 6 index}/${baseNameOf file}" (source file)
      ) value
    else
      [ (lib.nameValuePair alias (source value)) ];

  entries = lib.concatLists (lib.mapAttrsToList bind inputs);
  boundInputs = builtins.listToAttrs entries;
  plan = {
    inherit
      name
      transform
      outputs
      configuration
      ;
    inputs =
      if builtins.length entries == builtins.length (builtins.attrNames boundInputs) then
        boundInputs
      else
        throw "${name}: duplicate stage input path";
    orderOnly = map toString needs;
  };

  runtime = {
    packages = lib.unique (nativeBuildInputs ++ lib.concatMap (step: step.packages) inlineInputs);
    pythonPackages =
      p: lib.unique (pythonPackages p ++ lib.concatMap (step: step.pythonPackages p) inlineInputs);
    licensed = lib.unique (siteTools ++ lib.concatMap (step: step.licensed) inlineInputs);
  };

  package = import ./stage.nix {
    inherit pkgs plan;
    python = python runtime.pythonPackages;
    inherit (runtime) packages licensed;
  };
in
{
  inherit package;
  artifacts = lib.mapAttrs (output: path: "${lib.getOutput output package}/${path}") outputs;
  inline = lib.mapAttrs (
    output: _:
    runtime
    // {
      inherit plan output;
      __corerefInline = true;
    }
  ) outputs;
}
