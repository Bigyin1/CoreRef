# Use DFT views of dependencies where available.
{
  lib,
  stage,
  files,
  tcl,
  defines,
  rtlInputs,
  all,
  synthesizable,
  simulate,
  hasTestbench,
  hierarchical,
}:
let
  insert =
    name: components: extraOutputs:
    stage (
      {
        inherit name;
        transform = "rtl:assemble_dft";
        inputs = {
          files = files.rtl;
          script = files.dftScript;
        }
        // rtlInputs components (views: views.dft or views.rtl);
        configuration = { inherit defines; };
        outputs = {
          out = "hdl";
        }
        // extraOutputs;
      }
      // tcl
    );
  dft = insert "dft" synthesizable (lib.optionalAttrs hierarchical { module = "dft_module.v"; });
  forSimulation = insert "dft-simulation" all { };
in
{
  artifact = dft.artifacts.out;
  views = lib.optionalAttrs hierarchical { dft = dft.artifacts.module; };
  packages = {
    dft = dft.package;
  }
  // lib.optionalAttrs hasTestbench {
    dft-simulation = forSimulation.package;
    simulate-dft = (simulate "simulate-dft" forSimulation.artifacts.out [ "COREREF_DFT" ]).package;
  };
}
