# Apply one declarative route profile to a project's files and dependencies.
{
  lib,
  recipe,
  system,
  flowSource,
  defaults,
  route,
}:
spec:
{
  name,
  source,
  profile,
  files ? { },
  parameters ? { },
  dependencies ? (_: [ ]),
  siteTools ? { },
}:
let
  defaultFile =
    key: path:
    if
      lib.any (
        part:
        builtins.elem part [
          ""
          "."
          ".."
        ]
      ) (lib.splitString "/" path)
    then
      throw "${name}: default file '${key}' must be relative to the Flow"
    else
      flowSource + "/${path}";
  projectFiles = lib.mapAttrs defaultFile (recipe.files or { }) // files;
  available = path: !builtins.isPath path || builtins.pathExists path;
  hasFile =
    key:
    let
      value = projectFiles.${key};
    in
    projectFiles ? ${key}
    && (if builtins.isList value then value != [ ] && lib.all available value else available value);
  rules = map (rule: if lib.all hasFile rule.whenFiles then rule else rule.otherwise or { }) (
    spec.rules or [ ]
  );
  use = spec.use ++ lib.concatMap (rule: rule.use or [ ]) rules;
  variants = (spec.variants or [ ]) ++ lib.concatMap (rule: rule.variants or [ ]) rules;
  views = lib.foldl' (merged: rule: merged // (rule.views or { })) (spec.views or { }) rules;

  components = dependencies system;
  bindings = import ./dependencies.nix {
    inherit lib name recipe;
    roots = components;
  };
  configuration =
    defaults
    // (recipe.parameters or { })
    // (spec.parameters or { })
    // bindings.parameters
    // parameters;
  result = route {
    inherit
      name
      use
      variants
      siteTools
      ;
    files = projectFiles;
    inherit (bindings) groups;
    parameters = configuration;
  };
  view =
    ref:
    if builtins.isAttrs ref then result.artifacts.${ref.stage}.${ref.output} else projectFiles.${ref};
in
{
  packages = result.packages // {
    default = result.packages.${spec.default};
  };
  component = {
    inherit name;
    kind = spec.kind or profile;
    id = builtins.toJSON [
      (toString source)
      parameters
    ];
    parameters = configuration;
    views = lib.mapAttrs (_: view) views;
    dependencies = components;
  };
}
