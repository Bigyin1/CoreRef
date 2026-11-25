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
  selectionRules = recipe.selections or { };
  selections = lib.mapAttrs (_: select) selectionRules;
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
      components = selected spec;
      stops = (selectionRules.${spec.selection or ""} or { }).stopAt or [ ];
      dependsOn =
        before: after:
        !(builtins.elem after.kind stops)
        && lib.any (dependency: dependency.name == before.name) after.dependencies;
      ordered =
        (lib.toposort dependsOn components).result or (throw "${name}: cycle in group '${group}'");
      entries = lib.concatLists (
        lib.imap0 (
          index: component:
          lib.mapAttrsToList (
            pattern: alternatives:
            lib.nameValuePair (builtins.replaceStrings
              [ "{index}" "{name}" ]
              [ (lib.fixedWidthNumber 6 index) component.name ]
              pattern
            ) (readView component alternatives)
          ) spec.files
        ) (if lib.any (lib.hasInfix "{index}") (builtins.attrNames spec.files) then ordered else components)
      );
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
