# Select the requested stages and identify the producer of each input file.
{ request }:
let
  inherit (builtins)
    attrNames
    concatMap
    foldl'
    genericClosure
    listToAttrs
    mapAttrs
    sort
    ;
  inherit (builtins.fromJSON request) stages targets publishing;
  keys = map (key: {
    inherit key;
  });
  needed = genericClosure {
    startSet = keys targets;
    operator =
      { key }:
      let
        stage = stages.${key};
      in
      keys (stage.needs ++ (if publishing then stage.checks else [ ]));
  };
  levels = mapAttrs (
    _: stage:
    foldl' (
      level: need:
      let
        after = levels.${need} + 1;
      in
      if level < after then after else level
    ) 0 stage.needs
  ) stages;
  outputs =
    stage:
    let
      files = stages.${stage}.outputs;
    in
    map (output: {
      name = files.${output};
      value = {
        inherit stage output;
      };
    }) (attrNames files);
  ordered = sort (a: b: levels.${a} < levels.${b}) (map (node: node.key) needed);
in
{
  plan = map (name: {
    inherit name;
    inputs = listToAttrs (concatMap outputs stages.${name}.needs);
  }) ordered;
}
