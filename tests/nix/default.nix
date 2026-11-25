# Exercise the stage contracts with ordinary derivations and one named output edge.
{ pkgs, core }:
let
  inherit (pkgs) lib;
  runtime = import ./runtime.nix { inherit pkgs core; };
  args = {
    inherit pkgs;
    python = _: runtime;
    name = "coreref-test";
    stageMap = ./stages.toml;
    use = [ "example" ];
    parameters.label = "ok";
  };
  example = core.mkRoute args;
  optionalArgs = args // {
    variants = [ "optional-input" ];
  };
  fallback = core.mkRoute optionalArgs;
  preferred = core.mkRoute (optionalArgs // { use = args.use ++ [ "optional" ]; });
  fails = value: !(builtins.tryEval value).success;
in
assert fails (core.mkRoute (args // { variants = [ "cycle" ]; }));
assert fails (core.mkRoute (args // { use = [ "missing" ]; }));
assert fails (
  core.mkRoute (
    args
    // {
      variants = [
        "optional-input"
        "unknown-primary"
      ];
    }
  )
);
assert fails (core.mkRoute (args // { variants = [ "bad-output" ]; })).artifacts.consume.out;
assert fails (
  core.mkRoute (
    args
    // {
      use = [
        "example"
        "example"
      ];
    }
  )
);
{
  stage-graph = pkgs.runCommand "coreref-stage-graph-test" { } ''
    test "$(cat ${example.artifacts.consume.out})" = "summary ok"
    test "$(cat ${fallback.artifacts.consume.out})" = "summary ok"
    test "$(cat ${preferred.artifacts.consume.out})" = "data ok"
    test "$(cat ${lib.getOutput "summary" example.packages.summary}/summary.txt)" = "summary"
    touch "$out"
  '';
}
