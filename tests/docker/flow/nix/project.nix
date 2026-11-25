# Bind project files and component views to the shared stage map.
{
  lib,
  mkRoute,
  platform,
  system,
}:
{
  name,
  source,
  files,
  kind ? "rtl",
  use ? [ "rtl" ],
  dependencies ? (_: [ ]),
  dftReadyIf ? null,
  macroInstance ? "hard_macro",
  siteTools ? { },
}:
let
  deps = dependencies system;
  top = builtins.replaceStrings [ "-" ] [ "_" ] name;
  id = toString source;
  scriptDir = ../src/coreref_flow/stages/scripts;
  defaultScripts = lib.genAttrs [
    "common"
    "floorplan"
    "place"
    "cts"
    "route"
  ] (action: scriptDir + "/${action}.tcl");
  rtlFile = builtins.path {
    path = files.rtl;
    name = "${name}.v";
  };

  dft = builtins.elem "dft" use && (dftReadyIf == null || builtins.pathExists dftReadyIf);
  simulation = files ? testbench;
  rtlSynthesis = !dft && builtins.elem "backend" use;
  selected =
    lib.remove "dft" use
    ++ lib.optional dft "dft"
    ++ lib.optional rtlSynthesis "rtl-synthesis"
    ++ lib.optional simulation "simulation"
    ++ lib.optional (simulation && dft) "dft-simulation";

  node = component: {
    key = "${component.name}:${component.id}";
    inherit component;
  };
  visible =
    stopAtMacros:
    let
      closure = builtins.genericClosure {
        startSet = map node deps;
        operator =
          { component, ... }:
          if stopAtMacros && component.kind == "macro" then [ ] else map node component.dependencies;
      };
      components = map (entry: entry.component) closure;
      names = map (component: component.name) components;
    in
    if builtins.length (lib.unique names) != builtins.length names then
      throw "${name}: conflicting component revisions in the dependency graph"
    else
      components;
  physical = visible true;
  rtlDeps = builtins.filter (component: component.kind != "macro") physical;
  macros = builtins.filter (component: component.kind == "macro") physical;
  macro =
    if macros == [ ] then
      null
    else if builtins.length macros == 1 then
      builtins.head macros
    else
      throw "${name}: the example backend supports one physical macro";

  sources =
    scan: components:
    lib.listToAttrs (
      map (
        component:
        lib.nameValuePair "sources/${component.name}.v" (
          if scan && component.artifacts ? dft.module then component.artifacts.dft.module else component.rtl
        )
      ) components
    );

  route = mkRoute {
    inherit name siteTools;
    use = selected;
    variants =
      lib.optional (kind == "dft" && dft) "hierarchical-dft"
      ++ lib.optional rtlSynthesis "rtl-synthesis"
      ++ lib.optional (builtins.elem "check-netlist" use) "checked-synthesis";
    files = defaultScripts // files // { inherit rtlFile; };
    parameters = {
      inherit top platform macroInstance;
      scan = kind == "dft";
      macro = if macro == null then "" else macro.top;
    };
    groups = {
      rtl = sources false (visible false);
      synthesizable = sources false rtlDeps;
      dft = sources true rtlDeps;
      dftSimulation = sources true (visible false);
      physicalLint =
        sources false rtlDeps
        // lib.mergeAttrsList (
          map (component: {
            "macros/${component.name}/abstract.lef" = component.artifacts.route.lef;
            "macros/${component.name}/timing.lib" = component.artifacts.route.liberty;
          }) macros
        );
      macroRtl = lib.optionalAttrs (macro != null) { "macro_rtl.v" = macro.rtl; };
      macroLib = lib.optionalAttrs (macro != null) { "macro.lib" = macro.artifacts.route.liberty; };
      macroLef = lib.optionalAttrs (macro != null) { "macro.lef" = macro.artifacts.route.lef; };
      macroGds = lib.optionalAttrs (macro != null) { "macro.gds" = macro.artifacts.gds.out; };
    };
  };
in
assert builtins.match "[A-Za-z_][A-Za-z0-9_]*" top != null;
assert builtins.elem kind [
  "rtl"
  "dft"
  "macro"
  "soc"
];
{
  packages = route.packages // {
    default = route.packages.rtl;
  };
  component = {
    inherit
      name
      kind
      id
      top
      ;
    rtl = rtlFile;
    inherit (route) artifacts;
    dependencies = deps;
  };
}
