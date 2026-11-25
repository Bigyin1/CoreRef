{
  inputs = {
    flow.url = "git+http://gitlab:18081/root/coreref-flow.git?ref=fixture";
    nixpkgs.follows = "flow/nixpkgs";
    increment = {
      url = "git+http://gitlab:18081/root/increment.git?ref=fixture";
      inputs.flow.follows = "flow";
    };
    counter = {
      url = "git+http://gitlab:18081/root/counter.git?ref=fixture";
      inputs.flow.follows = "flow";
      inputs.increment.follows = "increment";
    };
    status-register = {
      url = "git+http://gitlab:18081/root/status-register.git?ref=fixture";
      inputs.flow.follows = "flow";
      inputs.increment.follows = "increment";
    };
    sram = {
      url = "git+http://gitlab:18081/root/sram.git?ref=fixture";
      inputs.flow.follows = "flow";
    };
  };

  outputs =
    {
      self,
      flow,
      counter,
      status-register,
      sram,
      ...
    }:
    flow.lib.mkProject {
      name = "soc";
      source = self.outPath;
      profile = "soc";
      parameters.top = "soc";
      files = {
        rtl = ./rtl/rtl.v;
        testbench = ./rtl/testbench.sv;
        constraints = ./be/constraints.sdc;
        place = ./be/place.tcl;
        dftReady = ./dft/enabled;
        dftScript = ./dft/run.tcl;
      };
      dependencies = system: [
        (counter.lib.componentFor system)
        (status-register.lib.componentFor system)
        (
          (sram.lib.mkSram {
            depth = 4;
            width = 4;
          }).lib.componentFor
            system
        )
        (
          (sram.lib.mkSram {
            depth = 8;
            width = 8;
          }).lib.componentFor
            system
        )
      ];
    };
}
