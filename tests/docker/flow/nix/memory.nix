# Generate and simulate one SRAM geometry.
{
  stage,
  template,
  body,
  lib,
  testbench,
  configuration,
  verilator,
}:
let
  model =
    name: inputs:
    stage {
      inherit name inputs;
      transform = "memory:generate";
      configuration = { inherit (configuration) depth width; };
      outputs.out = "hdl";
    };
  rtl = model "rtl" ({ inherit template; } // lib.optionalAttrs (body != null) { inherit body; });
  simulate = stage (
    {
      name = "simulate";
      transform = "verification:simulate_sram";
      inputs = {
        rtl = rtl.artifacts.out;
        inherit testbench;
      };
      configuration = { inherit (configuration) top depth width; };
      outputs.out = "simulation.log";
    }
    // verilator
  );
in
{
  packages = {
    default = rtl.package;
    rtl = rtl.package;
    simulate = simulate.package;
  };
  views = {
    rtl = rtl.artifacts.out;
  }
  // lib.optionalAttrs (body != null) {
    blackbox = (model "blackbox" { inherit template; }).artifacts.out;
  };
}
