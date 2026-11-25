# Generate and simulate one SRAM geometry.
{
  stage,
  template,
  model,
  testbench,
  configuration,
  verilator,
}:
let
  rtl = stage {
    name = "rtl";
    transform = "memory:generate";
    inputs = { inherit template; };
    configuration = { inherit (configuration) depth width defines; };
    outputs.out = "hdl";
  };
  simulate = stage (
    {
      name = "simulate";
      transform = "verification:simulate_sram";
      inputs = {
        rtl = model.artifacts.out;
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
    default = model.package;
    rtl = model.package;
    simulate = simulate.package;
  };
  source = rtl.artifacts.out;
  views.rtl = model.artifacts.out;
}
