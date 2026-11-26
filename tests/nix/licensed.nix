# An opt-in worker fixture. The site setup controls the version it reports.
{
  pkgs,
  coreref,
  mkStage,
}:
let
  runtime = import ./runtime.nix { inherit pkgs coreref; };
  stage =
    mkStage
      {
        inherit pkgs;
        python = _: runtime;
      }
      {
        name = "coreref-test-licensed-test";
        transform = "coreref_test_stages:licensed";
        outputs.out = "result.txt";
        siteTools = [
          {
            profile = "test-v1";
            executable = "example-tool";
            module = "example/1.2";
            version = "1.2-hf3";
          }
        ];
      };
in
{
  licensed-test = stage.package;
}
