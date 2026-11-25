{
  inputs = {
    flow.url = "git+http://gitlab:18081/root/coreref-flow.git?ref=fixture";
    nixpkgs.follows = "flow/nixpkgs";
  };

  outputs =
    { self, flow, ... }:
    let
      mkRtl =
        {
          step ? 1,
        }:
        assert step == 1 || step == 2 || throw "increment: step must be 1 or 2";
        flow.lib.mkProject {
          name = if step == 1 then "increment" else "increment-by-two";
          source = self.outPath;
          profile = "rtl";
          parameters = {
            top = "increment";
            verificationDefines = [ "TB_STEP=${toString step}" ];
          };
          rtl = {
            sources = [
              (if step == 1 then ./rtl/step_one.v else ./rtl/step_two.v)
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
