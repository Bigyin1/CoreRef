# Select the views used by behavioral and physical stages.
{
  lib,
  name,
  roots,
  selectComponents,
}:
let
  isMacro = component: component.kind == "macro";
  all = selectComponents { inherit roots; };
  synthesizable = selectComponents {
    inherit roots;
    stopAt = isMacro;
    filter = component: !isMacro component;
  };
  macros = selectComponents {
    inherit roots;
    stopAt = isMacro;
    filter = isMacro;
  };
  macro =
    if macros == [ ] then
      null
    else if builtins.length macros == 1 then
      builtins.head macros
    else
      throw "${name}: the physical route expects at most one macro";
  rtlInputs =
    components: view:
    builtins.listToAttrs (
      lib.imap0 (
        index: component:
        lib.nameValuePair "sources/${lib.fixedWidthNumber 6 index}-${component.name}" (view component.views)
      ) components
    );
  abstracts = lib.mergeAttrsList (
    map (component: {
      "macros/${component.name}/abstract.lef" = component.views.lef;
      "macros/${component.name}/timing.lib" = component.views.liberty;
    }) macros
  );

in
{
  inherit
    all
    synthesizable
    macro
    abstracts
    rtlInputs
    ;
}
