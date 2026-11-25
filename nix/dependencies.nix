# Select reachable components in dependency order.
{ lib }:
{
  roots,
  stopAt ? (_: false),
  filter ? (_: true),
  viewKey ? (_: null),
}:
let
  children = component: lib.optionals (!(stopAt component)) component.dependencies;
  entries =
    key: parents:
    lib.imap0 (
      index: component:
      let
        chain = parents ++ [ component.name ];
      in
      if lib.elem component.name parents then
        throw "cycle in component dependencies: ${lib.concatStringsSep " -> " chain}"
      else
        {
          key = key ++ [ index ];
          inherit chain component;
        }
    );
  # Explore each occurrence before deduplicating. Equal revisions can have different inputs.
  reachable = builtins.genericClosure {
    startSet = entries [ ] [ ] roots;
    operator = node: entries node.key node.chain (children node.component);
  };
  identity = component: [
    component.name
    component.id
  ];
  selection = component: {
    included = filter component;
    stopped = stopAt component;
    view = if filter component then viewKey component else null;
  };
  record =
    seen: node:
    let
      inherit (node) component;
      inherit (component) name;
      previous = seen.${name} or null;
    in
    if previous == null then
      seen // { ${name} = node; }
    else if
      previous.component.id == component.id
      && selection previous.component == selection component
      && map identity (children previous.component) == map identity (children component)
    then
      seen
    else
      throw ''
        conflicting revisions or views in the dependency graph for '${name}'
          ${lib.concatStringsSep " -> " previous.chain}
          ${lib.concatStringsSep " -> " node.chain}
      '';
  seen = builtins.foldl' record { } reachable;
  names = lib.unique (map (node: node.component.name) reachable);
  unique = builtins.seq seen (map (name: seen.${name}.component) names);
  dependsOn = before: after: lib.any (dependency: dependency.name == before.name) (children after);
  ordered = lib.toposort dependsOn unique;
in
builtins.filter filter (ordered.result or (throw "cycle in component dependencies"))
