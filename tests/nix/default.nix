# Exercise a named output both as a derivation edge and as inline preparation.
{ pkgs, core }:
let
  inherit (pkgs) lib;
  runtime = import ./runtime.nix { inherit pkgs core; };
  stage = core.mkStage {
    inherit pkgs;
    python = _: runtime;
  };
  producer = stage {
    name = "coreref-test-produce";
    transform = "coreref_test_stages:produce";
    outputs = {
      out = "data.txt";
      summary = "summary.txt";
    };
  };
  consume =
    name: summary:
    stage {
      inherit name;
      transform = "coreref_test_stages:consume";
      inputs."summary.txt" = summary;
      outputs.out = "result.txt";
      configuration.label = "ok";
    };
  materialized = consume "coreref-test-materialized" producer.artifacts.summary;
  inline = consume "coreref-test-inline" producer.inline.summary;
  consumeDirectories = stage {
    name = "coreref-test-outputs";
    transform = "coreref_test_stages:consume_directories";
    inputs = {
      data = producer.package;
      summary = lib.getOutput "summary" producer.package;
      ordered = [
        producer.package
        (lib.getOutput "summary" producer.package)
      ];
    };
    outputs.out = "result.txt";
  };
  producerDrv = builtins.unsafeDiscardStringContext producer.package.drvPath;
  fails = value: !(builtins.tryEval (builtins.deepSeq value true)).success;
  select = core.selectComponents;
  names = map (component: component.name);
  node = name: id: dependencies: { inherit name id dependencies; };
  leaf = node "leaf" "1" [ ];
  mid = node "mid" "1" [ leaf ];
  left = node "left" "1" [ mid ];
  right = node "right" "1" [ mid ];
  conflictingRight = node "right" "1" [
    (node "mid" "1" [ (node "leaf" "2" [ ]) ])
  ];
  cycle = node "cycle" "1" [ cycle ];
  opaque = node "macro" "1" (throw "opaque macro inputs were inspected");
  cut = component: component.name == "macro";
in
assert
  names (select {
    roots = [ left ];
    filter = component: component.name != "mid";
  }) == [
    "leaf"
    "left"
  ];
assert
  names (select {
    roots = [
      left
      right
    ];
  }) == [
    "leaf"
    "mid"
    "left"
    "right"
  ];
assert fails (select {
  roots = [
    left
    conflictingRight
  ];
});
assert fails (select {
  roots = [ cycle ];
});
assert
  names (select {
    roots = [ opaque ];
    stopAt = cut;
  }) == [ "macro" ];
assert builtins.elem "leaf" (
  names (select {
    roots = [
      opaque
      left
    ];
    stopAt = cut;
  })
);
assert fails (consume "coreref-test-invalid" "unbound-file").package.drvPath;
assert fails
  (stage {
    name = "coreref-test-unsafe-path";
    transform = "coreref_test_stages:produce";
    outputs.out = "../data.txt";
  }).package.drvPath;
assert builtins.hasAttr producerDrv (builtins.getContext materialized.package.stagePlan);
assert !(builtins.hasAttr producerDrv (builtins.getContext inline.package.stagePlan));
{
  stage-graph = pkgs.runCommand "coreref-stage-graph-test" { } ''
    test "$(cat ${materialized.artifacts.out})" = "summary ok"
    test "$(cat ${inline.artifacts.out})" = "summary ok"
    test "$(cat ${lib.getOutput "summary" producer.package}/summary.txt)" = "summary"
    test "$(cat ${consumeDirectories.artifacts.out})" = "data summary"
    touch "$out"
  '';
}
