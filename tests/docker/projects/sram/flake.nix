{
  inputs = {
    flow.url = "git+http://gitlab:18081/root/coreref-flow.git?ref=fixture";
    nixpkgs.follows = "flow/nixpkgs";
  };

  outputs =
    { self, flow, ... }:
    flow.lib.mkProject {
      name = "sram";
      source = self.outPath;
      profile = "memory";
      files = {
        template = ./memory.v.in;
        testbench = ./testbench.sv;
      };
      options = {
        depth = [
          4
          8
        ];
        width = [
          4
          8
        ];
      };
    };
}
