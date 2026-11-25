# Resolve component views for named stage input groups.
{
  lib,
  name,
  recipe,
  roots,
}:
let
  entry = component: {
    key = [
      component.name
      component.id
    ];
    inherit component;
  };
  walk =
    stopAt:
    map (node: node.component) (
      builtins.genericClosure {
        startSet = map entry roots;
        operator =
          { component, ... }:
          lib.optionals (!(builtins.elem component.kind stopAt)) (map entry component.dependencies);
      }
    );
  select =
    selection:
    let
      components = walk (selection.stopAt or [ ]);
      names = map (component: component.name) components;
      kinds = selection.kinds or null;
      exclude = selection.excludeKinds or [ ];
    in
    if lib.unique names != names then
      throw "${name}: conflicting component revisions in the dependency graph"
    else
      builtins.filter (
        component:
        (kinds == null || builtins.elem component.kind kinds) && !(builtins.elem component.kind exclude)
      ) components;
  selections = lib.mapAttrs (_: select) (recipe.selections or { });
  all = select { };
  selected =
    rule:
    if rule ? selection then
      selections.${rule.selection} or (throw "${name}: unknown dependency selection '${rule.selection}'")
    else
      all;

  readView =
    component: alternatives:
    let
      view = lib.findFirst (key: component.views ? ${key}) null alternatives;
    in
    if view == null then
      throw "${name}: '${component.name}' provides none of ${lib.concatStringsSep ", " alternatives}"
    else
      component.views.${view};
  groupFiles =
    group: spec:
    let
      entries = lib.concatMap (
        component:
        lib.mapAttrsToList (
          pattern: alternatives:
          lib.nameValuePair (builtins.replaceStrings [ "{name}" ] [ component.name ] pattern) (
            readView component alternatives
          )
        ) spec.files
      ) (selected spec);
      files = builtins.listToAttrs entries;
    in
    if builtins.length entries != builtins.length (builtins.attrNames files) then
      throw "${name}: duplicate input path in group '${group}'"
    else
      files;
  dependencyParameter =
    key: rule:
    let
      components = selected rule;
    in
    if components == [ ] then
      rule.default
    else if builtins.length components == 1 then
      (builtins.head components).parameters.${rule.parameter}
    else
      throw "${name}: parameter '${key}' expects a single dependency";
in
{
  groups = lib.mapAttrs groupFiles (recipe.groups or { });
  parameters = lib.mapAttrs dependencyParameter (recipe.dependencyParameters or { });
}
