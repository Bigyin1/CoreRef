# Generate and simulate one SRAM geometry.
{
  stage,
  files,
  configuration,
  verilator,
}:
let
  rtl = stage {
    name = "rtl";
    transform = "memory:generate";
    inputs.template = files.template;
    configuration = { inherit (configuration) depth width; };
    outputs.out = "hdl";
  };
  simulate = stage (
    {
      name = "simulate";
      transform = "verification:simulate_sram";
      inputs = {
        rtl = rtl.artifacts.out;
        inherit (files) testbench;
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
  views.rtl = rtl.artifacts.out;
}
