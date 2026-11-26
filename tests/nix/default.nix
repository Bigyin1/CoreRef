# Exercise named outputs and separate stage environments.
{
  pkgs,
  coreref,
  mkStage,
}:
let
  inherit (pkgs) lib;
  runtime = import ./runtime.nix { inherit pkgs coreref; };
  stage = mkStage {
    inherit pkgs;
    python = _: runtime;
  };
  tool = label: pkgs.writeShellScriptBin "stage-tool" "echo ${label}";
  producer = stage {
    name = "coreref-test-produce";
    transform = "coreref_test_stages:produce";
    nativeBuildInputs = [ (tool "producer") ];
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
      nativeBuildInputs = [ (tool "consumer") ];
      inputs."summary.txt" = summary;
      outputs.out = "result.txt";
      configuration.label = "ok";
    };
  materialized = consume "coreref-test-materialized" producer.artifacts.summary;
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

in
assert fails (consume "coreref-test-invalid" "unbound-file").package.drvPath;
assert fails
  (stage {
    name = "coreref-test-unsafe-path";
    transform = "coreref_test_stages:produce";
    outputs.out = "../data.txt";
  }).package.drvPath;
assert builtins.hasAttr producerDrv (builtins.getContext materialized.package.stagePlan);
{
  stage-graph = pkgs.runCommand "coreref-stage-graph-test" { } ''
    test "$(cat ${materialized.artifacts.out})" = "producer consumer ok"
    test "$(cat ${lib.getOutput "summary" producer.package}/summary.txt)" = "producer"
    test "$(cat ${consumeDirectories.artifacts.out})" = "data producer"
    touch "$out"
  '';
}
