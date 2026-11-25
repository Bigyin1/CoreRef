{
  inputs = {
    flow.url = "git+http://gitlab:18081/root/coreref-flow.git?ref=fixture";
    nixpkgs.follows = "flow/nixpkgs";
  };

  outputs =
    { self, flow, ... }:
    flow.lib.mkMemory {
      name = "sram";
      source = self.outPath;
      template = ./memory.v.in;
      testbench = ./testbench.sv;
      depths = [
        4
        8
      ];
      widths = [
        4
        8
      ];
    };
}
