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
  producerDrv = builtins.unsafeDiscardStringContext producer.package.drvPath;
  fails = value: !(builtins.tryEval value).success;
in
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
    touch "$out"
  '';
}
