# Assemble behavioral and synthesizable sources and run verification on them.
{
  lib,
  stage,
  files,
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
        files = files.rtl;
      }
      // rtlInputs components (views: views.rtl);
      configuration.defines = stageDefines;
      outputs.out = "hdl";
    };
  rtl = assemble "rtl" all defines;
  forSynth = assemble "rtl-synthesis-input" synthesizable defines;

  simulate =
    name: input: extraDefines:
    stage (
      {
        inherit name;
        transform = "verification:simulate";
        inputs = {
          rtl = input;
          inherit (files) testbench;
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
          inputs.rtl = rtl.inline.out;
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
          rtl = forSynth.inline.out;
        }
        // abstracts;
        configuration = { inherit top; };
        outputs.out = "inputs.json";
      }).package;
  }
  // lib.optionalAttrs hasTestbench {
    simulate = (simulate "simulate" rtl.inline.out [ ]).package;
  };
}
