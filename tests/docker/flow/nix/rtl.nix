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
  inherit (configuration) top defines;
  assemble =
    name: components:
    stage {
      inherit name;
      transform = "rtl:assemble";
      inputs = {
        files = files.rtl;
      }
      // rtlInputs components (views: views.rtl);
      outputs.out = "hdl";
    };
  rtl = assemble "rtl" all;
  forSynth = assemble "rtl-synthesis-input" synthesizable;

  simulate =
    name: input: extra:
    stage (
      {
        inherit name;
        transform = "verification:simulate";
        inputs = {
          rtl = input;
          inherit (files) testbench;
        };
        configuration = {
          inherit defines;
        }
        // extra;
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
          configuration = { inherit top defines; };
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
    simulate = (simulate "simulate" rtl.inline.out { }).package;
  };
}
