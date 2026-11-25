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
      profile = "macro";
      parameters.top = "counter";
      files = {
        rtl = ./rtl/rtl.v;
        testbench = ./rtl/testbench.sv;
        constraints = ./be/constraints.sdc;
        dftScript = ./dft/run.tcl;
      };
      dependencies = system: [ (increment.lib.componentFor system) ];
    };
}
