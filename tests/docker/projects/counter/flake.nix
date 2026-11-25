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
      parameters = {
        top = "counter";
        defines = [ "COUNTER_RESET=0" ];
      };
      rtl = {
        sources = [ ./rtl/rtl.v ];
        testbench = ./rtl/testbench.sv;
      };
      dft = {
        enabled = true;
        script = ./dft/run.tcl;
      };
      backend.constraints = ./be/constraints.sdc;
      dependencies = system: [ (increment.lib.componentFor system) ];
    };
}
