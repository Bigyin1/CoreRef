# Assemble behavioral and synthesizable sources and run verification on them.
{
  lib,
  stage,
  model,
  sourceFiles,
  testbench,
  configuration,
  verilator,
  rtlInputs,
  all,
  synthesizable,
  abstracts,
  hasTestbench,
}:
let
  inherit (configuration) top defines verificationDefines;
  assemble =
    name: components: stageDefines:
    stage {
      inherit name;
      transform = "rtl:assemble";
      inputs = {
        files = sourceFiles;
      }
      // rtlInputs components (views: views.rtl);
      configuration.defines = stageDefines;
      outputs.out = "hdl";
    };
  sources = assemble "rtl" all defines;
  rtl = if model == null then sources else model;
  forSynth = assemble "rtl-synthesis-input" synthesizable defines;

  simulate =
    name: input: extraDefines:
    stage (
      {
        inherit name;
        transform = "verification:simulate";
        inputs = {
          rtl = input;
          inherit testbench;
        };
        configuration = {
          defines = verificationDefines ++ extraDefines;
        };
        outputs.out = "simulation.log";
      }
      // verilator
    );
in
{
  inherit forSynth simulate;
  packages = {
    default = rtl.package;
    rtl = rtl.package;
    rtl-synthesis-input = forSynth.package;
    lint =
      (stage (
        {
          name = "lint";
          transform = "verification:lint";
          inputs.rtl = sources.artifacts.out;
          configuration = { inherit top; };
          outputs.out = "lint.log";
        }
        // verilator
      )).package;
    lint-physical =
      (stage {
        name = "lint-physical";
        transform = "physical_lint:check_inputs";
        inputs = {
          rtl = forSynth.artifacts.out;
        }
        // abstracts;
        configuration = { inherit top; };
        outputs.out = "inputs.json";
      }).package;
  }
  // lib.optionalAttrs hasTestbench {
    simulate = (simulate "simulate" rtl.artifacts.out [ ]).package;
  };
}
