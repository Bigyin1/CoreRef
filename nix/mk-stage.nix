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

  source =
    path:
    if builtins.isPath path then
      builtins.path {
        inherit path;
        name = baseNameOf path;
      }
    else if lib.isDerivation path then
      toString path
    else if builtins.isString path && builtins.hasContext path then
      path
    else
      throw "${name}: stage inputs must be Nix paths or derivation outputs";

  bind =
    alias: value:
    if builtins.isList value then
      lib.imap0 (
        index: file:
        let
          basename = builtins.unsafeDiscardStringContext (baseNameOf (toString file));
        in
        lib.nameValuePair "${alias}/${lib.fixedWidthNumber 6 index}/${basename}" (source file)
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

  package = import ./stage.nix {
    inherit pkgs plan;
    python = python pythonPackages;
    packages = nativeBuildInputs;
    licensed = siteTools;
  };
in
{
  inherit package;
  artifacts = lib.mapAttrs (output: path: "${lib.getOutput output package}/${path}") outputs;
}
