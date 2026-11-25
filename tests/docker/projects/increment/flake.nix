{
  inputs = {
    flow.url = "git+http://gitlab:18081/root/coreref-flow.git?ref=fixture";
    nixpkgs.follows = "flow/nixpkgs";
  };

  outputs =
    { self, flow, ... }:
    let
      sources = {
        "1" = ./rtl/step_one.v;
        "2" = ./rtl/step_two.v;
        "3" = ./rtl/step_three.v;
      };
      mkRtl =
        {
          step ? 1,
        }:
        assert
          builtins.elem step [
            1
            2
            3
          ]
          || throw "increment: step must be 1, 2 or 3";
        flow.lib.mkProject {
          name = if step == 1 then "increment" else "increment-by-${if step == 2 then "two" else "three"}";
          source = self.outPath;
          profile = "rtl";
          parameters = {
            top = "increment";
            verificationDefines = [ "TB_STEP=${toString step}" ];
          };
          rtl = {
            sources = [
              sources.${toString step}
              ./rtl/increment.v
            ];
            testbench = ./rtl/increment_tb.sv;
          };
        };
      default = mkRtl { };
    in
    default
    // {
      lib = default.lib // {
        inherit mkRtl;
      };
    };
}
