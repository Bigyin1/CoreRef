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
      name = "status-register";
      source = self.outPath;
      profile = "dft";
      parameters.top = "status_register";
      files = {
        rtl = ./rtl/rtl.v;
        testbench = ./rtl/testbench.sv;
      };
      dependencies = system: [ (increment.lib.componentFor system) ];
    };
}
