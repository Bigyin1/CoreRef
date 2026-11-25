# One SRAM source input exposes an independent derivation for each geometry.
{
  lib,
  mkRoute,
}:
{
  name,
  source,
  template,
  testbench,
  depths,
  widths,
}:
let
  build =
    depth: width:
    let
      valid = builtins.elem depth depths && builtins.elem width widths;
      geometry = "${toString depth}x${toString width}";
      top = "${name}_${geometry}";
      route =
        if valid then
          mkRoute {
            name = "${name}-${geometry}";
            use = [ "memory" ];
            files = { inherit template testbench; };
            parameters = { inherit depth width top; };
          }
        else
          throw "${name}: unsupported SRAM geometry ${geometry}";
    in
    {
      package = route.packages.rtl;
      component = {
        name = "${name}-${geometry}";
        inherit top;
        id = toString source;
        kind = "rtl";
        rtl = route.artifacts.rtl.out;
        inherit (route) artifacts;
        dependencies = [ ];
      };
      packages = {
        "${geometry}" = route.packages.rtl;
        "simulate-${geometry}" = route.packages.simulate;
      };
    };
  geometries = lib.concatMap (depth: map (width: build depth width) widths) depths;
  default = build (builtins.head depths) (builtins.head widths);
in
assert depths != [ ] && lib.all (depth: builtins.isInt depth && depth >= 2) depths;
assert widths != [ ] && lib.all (width: builtins.isInt width && width >= 1) widths;
{
  packages = lib.mergeAttrsList (map (item: item.packages) geometries) // {
    default = default.package;
  };
  component = default.component;
  variant = { depth, width }: (build depth width).component;
}
