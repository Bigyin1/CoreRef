{
  inputs = {
    flow.url = "git+http://gitlab:18081/root/coreref-flow.git?ref=fixture";
    nixpkgs.follows = "flow/nixpkgs";
  };

  outputs =
    { self, flow, ... }:
    flow.lib.mkProject {
      name = "increment";
      source = self.outPath;
      files = {
        rtl = ./rtl/increment.v;
        testbench = ./rtl/increment_tb.sv;
      };
    };
}
