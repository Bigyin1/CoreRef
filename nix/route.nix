# Compose a stage map into derivations and expose each declared artifact.
{
  pkgs,
  python,
  name,
  stageMap,
  use,
  variants ? [ ],
  files ? { },
  groups ? { },
  parameters ? { },
  tools ? { },
  siteTools ? { },
}:
let
  inherit (pkgs) lib;
  inherit (builtins)
    attrNames
    attrValues
    concatLists
    elem
    hasAttr
    isAttrs
    isPath
    length
    mapAttrs
    ;
  map = builtins.fromTOML (builtins.readFile stageMap);

  lookup =
    values: category: key:
    values.${key} or (throw "${name}: unknown ${category} '${key}'");

  collect =
    category:
    lib.foldl' (
      result: entry:
      if hasAttr entry.name result then
        throw "${name}: duplicate ${category} '${entry.name}'"
      else
        result // { ${entry.name} = entry.value; }
    ) { };

  patches = builtins.map (lookup (map.variants or { }) "route variant") variants;
  base = collect "stage" (
    lib.concatMap (
      module: lib.mapAttrsToList lib.nameValuePair (lookup map.modules "route module" module)
    ) use
  );
  selected = lib.foldl' (
    current: patch:
    current
    // mapAttrs (
      stage: delta: lib.recursiveUpdate (lookup current "stage in route variant" stage) delta
    ) patch
  ) base patches;

  declaredStages = lib.concatMap attrNames (attrValues map.modules);
  inputSource =
    ref:
    if isAttrs ref && ref ? fallback && !(hasAttr ref.stage selected) then
      assert elem ref.stage declaredStages || throw "${name}: unknown stage dependency '${ref.stage}'";
      ref.fallback
    else
      ref;
  specs = mapAttrs (
    _: spec: spec // { inputs = mapAttrs (_: inputSource) (spec.inputs or { }); }
  ) selected;
  inputStages =
    spec: builtins.map (input: input.stage) (builtins.filter isAttrs (attrValues spec.inputs));
  predecessors = spec: (spec.needs or [ ]) ++ inputStages spec;
  graph = lib.toposort (before: after: elem before (predecessors specs.${after})) (attrNames specs);
  requireKnownStages = lib.all (stage: builtins.seq (lookup specs "stage dependency" stage) true) (
    lib.concatMap predecessors (attrValues specs)
  );
  validation =
    assert requireKnownStages;
    if graph ? cycle then
      throw "${name}: cycle in stage dependencies (${lib.concatStringsSep ", " graph.cycle})"
    else
      true;

  # Give a source file a store identity independent of the rest of its checkout.
  source =
    file:
    if isPath file then
      builtins.path {
        path = file;
        name = baseNameOf file;
      }
    else if (builtins.isString file && builtins.hasContext file) || lib.isDerivation file then
      file
    else
      throw "${name}: stage inputs must be Nix paths or derivation outputs";

  bind =
    input:
    if isAttrs input then
      lookup (lookup artifacts "stage" input.stage) "output of ${input.stage}" (input.output or "out")
    else
      source (lookup files "project file" input);

  stageFor =
    stage: spec:
    let
      explicit = lib.mapAttrsToList (alias: value: lib.nameValuePair alias (bind value)) spec.inputs;
      groupFiles = lib.concatMap (
        group:
        lib.mapAttrsToList (alias: value: lib.nameValuePair alias (source value)) (
          lookup groups "input group" group
        )
      ) (spec.groups or [ ]);
      toolNames = spec.tools or [ ];
      nativePackages = concatLists (
        builtins.map (lookup tools "tool") (builtins.filter (tool: !(hasAttr tool siteTools)) toolNames)
      );
      licensed = builtins.map (lookup siteTools "site tool") (
        builtins.filter (tool: hasAttr tool siteTools) toolNames
      );
      boundParams = lib.genAttrs (spec.parameters or [ ]) (lookup parameters "parameter");
      filesFromStages = inputStages spec;
      orderOnly = builtins.map (dep: stages.${dep}.out) (
        builtins.filter (dep: !(elem dep filesFromStages)) (spec.needs or [ ])
      );
    in
    import ./stage.nix {
      inherit pkgs orderOnly licensed;
      python = python (spec.pythonPackages or [ ]);
      name = "${name}-${stage}";
      inherit (spec) transform outputs;
      configuration = boundParams // (spec.configuration or { });
      inputs = collect "input for ${stage}" (explicit ++ groupFiles);
      packages = nativePackages;
    };

  stages = mapAttrs stageFor specs;
  artifacts = mapAttrs (
    stage: spec: mapAttrs (output: file: "${stages.${stage}.${output}}/${file}") spec.outputs
  ) specs;
  aliases = lib.concatLists (
    lib.mapAttrsToList (
      stage: spec:
      lib.mapAttrsToList (
        alias: output:
        lib.nameValuePair alias (
          builtins.seq (lookup spec.outputs "output of ${stage}" output) (
            lib.getOutput output stages.${stage}
          )
        )
      ) (spec.exports or { })
    ) specs
  );
in
assert validation;
{
  inherit artifacts stages;
  packages = collect "target" (lib.mapAttrsToList lib.nameValuePair stages ++ aliases);
}
