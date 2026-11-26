# Select the views used by behavioral and physical stages.
{
  lib,
  roots,
}:
let
  selectComponents = import ./select-components.nix { inherit lib; };
  isMacro = component: component.kind == "macro";
  all =
    view:
    selectComponents {
      inherit roots;
      stopAt = isMacro;
      viewKey = component: view component.views;
    };
  synthesizable =
    view:
    selectComponents {
      viewKey = component: view component.views;
      inherit roots;
      stopAt = isMacro;
      filter = component: !isMacro component;
    };
  macros =
    view:
    selectComponents {
      viewKey = component: [
        component.parameters.top
        (view component.views)
      ];
      inherit roots;
      stopAt = isMacro;
      filter = isMacro;
    };
  rtlInputs =
    select: view:
    builtins.listToAttrs (
      lib.imap0 (
        index: component:
        lib.nameValuePair "sources/${lib.fixedWidthNumber 6 index}-${component.name}" (view component.views)
      ) (select view)
    );
  abstracts = lib.mergeAttrsList (
    map
      (component: {
        "macros/${component.name}/abstract.lef" = component.views.lef;
        "macros/${component.name}/timing.lib" = component.views.liberty;
      })
      (
        macros (views: [
          views.lef
          views.liberty
        ])
      )
  );

in
{
  inherit
    all
    synthesizable
    macros
    abstracts
    rtlInputs
    ;
}
