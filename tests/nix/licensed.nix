# An opt-in worker fixture. The site setup controls the version it reports.
{ pkgs, core }:
let
  runtime = import ./runtime.nix { inherit pkgs core; };
  route = core.mkRoute {
    inherit pkgs;
    python = _: runtime;
    name = "coreref-test";
    stageMap = ./licensed.toml;
    use = [ "test" ];
    siteTools.example = {
      profile = "test-v1";
      executable = "example-tool";
      module = "example/1.2-hf3";
      version = "1.2-hf3";
    };
  };
in
{
  licensed-test = route.packages.licensed-test;
}
