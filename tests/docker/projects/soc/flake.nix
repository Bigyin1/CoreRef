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
    let
      counterStep = 1;
      counterVariant = counter.lib.variants."plus-${toString counterStep}";
    in
    flow.lib.mkProject {
      name = "soc";
      source = self.outPath;
      profile = "soc";
      parameters = {
        top = "soc";
        verificationDefines = [ "TB_COUNTER_STEP=${toString counterStep}" ];
        macroPlacement = {
          hard_macro = [
            21
            47
          ];
          cache = [
            116
            47
          ];
          scratch = [
            211
            47
          ];
        };
      };
      rtl = {
        sources = [ ./rtl/rtl.v ];
        testbench = ./rtl/testbench.sv;
      };
      dft = {
        enabled = true;
        script = ./dft/run.tcl;
      };
      backend = {
        constraints = ./be/constraints.sdc;
        scripts.place = ./be/place.tcl;
      };
      dependencies = system: [
        (counterVariant.lib.componentFor system)
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
