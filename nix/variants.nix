# Publish parameter combinations as separate, lazily evaluated targets.
{
  lib,
  name,
  matrix,
  options,
  build,
  defaultTarget,
}:
let
  axes = builtins.attrNames matrix.axes;
  axisValues =
    key: values:
    let
      rule = matrix.axes.${key};
    in
    values != [ ]
    && lib.unique values == values
    && lib.all (
      value:
      if rule ? min then
        builtins.isInt value && value >= rule.min
      else
        builtins.isInt value || builtins.isString value
    ) values;
  allowed =
    if
      axes != [ ]
      && builtins.attrNames options == axes
      && lib.all (key: axisValues key options.${key}) axes
    then
      options
    else
      throw "${name}: invalid values for build options ${lib.concatStringsSep ", " axes}";
  render =
    values: pattern:
    let
      result = builtins.replaceStrings (map (key: "{${key}}") (builtins.attrNames values)) (map toString (
        builtins.attrValues values
      )) pattern;
    in
    if builtins.match "[A-Za-z0-9][A-Za-z0-9_.+-]*" result != null then
      result
    else
      throw "${name}: invalid target name '${result}'";
  instantiate =
    values:
    let
      suffix = render values matrix.name;
    in
    if
      builtins.attrNames values == axes && lib.all (key: builtins.elem values.${key} allowed.${key}) axes
    then
      (build "${name}-${suffix}" values) // { inherit suffix; }
    else
      throw "${name}: unsupported build options";
  entries = lib.concatMap (
    values:
    let
      variant = instantiate values;
    in
    lib.mapAttrsToList (
      pattern: target:
      lib.nameValuePair (render (
        values // { variant = variant.suffix; }
      ) pattern) variant.packages.${target}
    ) matrix.exports
  ) (lib.cartesianProduct allowed);
  targets = builtins.listToAttrs entries;
  default = instantiate (lib.mapAttrs (_: builtins.head) allowed);
in
{
  packages =
    assert
      !(targets ? default) && builtins.length entries == builtins.length (builtins.attrNames targets)
      || throw "${name}: duplicate build target";
    targets // { default = default.packages.${defaultTarget}; };
  component = default.component;
  variant = values: (instantiate values).component;
}
