# Select reachable components in dependency order.
{ lib }:
{
  roots,
  stopAt ? (_: false),
  filter ? (_: true),
}:
let
  entry = component: {
    key = [
      component.name
      component.id
    ];
    inherit component;
  };
  reachable = map (node: node.component) (
    builtins.genericClosure {
      startSet = map entry roots;
      operator =
        { component, ... }:
        lib.optionals (!(stopAt component)) (map entry component.dependencies);
    }
  );
  names = map (component: component.name) reachable;
  dependsOn =
    before: after:
    !(stopAt after) && lib.any (dependency: dependency.name == before.name) after.dependencies;
  ordered = lib.toposort dependsOn (builtins.filter filter reachable);
in
if lib.unique names != names then
  throw "conflicting component revisions in the dependency graph"
else
  ordered.result or (throw "cycle in component dependencies")
