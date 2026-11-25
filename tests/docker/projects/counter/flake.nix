{
  inputs = {
    flow.url = "git+http://gitlab:18081/root/coreref-flow.git?ref=fixture";
    nixpkgs.follows = "flow/nixpkgs";
    increment = {
      url = "git+http://gitlab:18081/root/increment.git?ref=fixture";
      inputs.flow.follows = "flow";
    };
  };

  outputs =
    {
      self,
      flow,
      increment,
      ...
    }:
    flow.lib.mkProject {
      name = "counter";
      source = self.outPath;
      kind = "macro";
      use = [
        "rtl"
        "dft"
        "backend"
      ];
      files = {
        rtl = ./rtl/rtl.v;
        testbench = ./rtl/testbench.sv;
        constraints = ./be/constraints.sdc;
      };
      dependencies = system: [ (increment.lib.componentFor system) ];
    };
}
