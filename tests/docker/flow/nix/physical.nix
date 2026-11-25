# The output of each physical stage is the input of the next one.
{
  lib,
  stage,
  constraints,
  scriptOverrides,
  macros,
  configuration,
  tools,
  checkSynthesis,
  rtl,
}:
let
  scripts = {
    common = ../src/coreref_flow/stages/scripts/common.tcl;
    floorplan = ../src/coreref_flow/stages/scripts/floorplan.tcl;
    place = ../src/coreref_flow/stages/scripts/place.tcl;
    cts = ../src/coreref_flow/stages/scripts/cts.tcl;
    route = ../src/coreref_flow/stages/scripts/route.tcl;
  }
  // scriptOverrides;
  macroInputs =
    suffix: view:
    builtins.listToAttrs (
      map (macro: lib.nameValuePair "macros/${macro.name}${suffix}" (view macro.views)) (macros view)
    );
  macroCells = map (macro: macro.parameters.top) (macros (_: null));
  placements = configuration.macroPlacement;
  synth = stage (
    {
      name = "synth";
      transform = "physical:synthesize";
      inputs = {
        inherit rtl;
      }
      // macroInputs "/blackbox" (views: views.blackbox or views.rtl);
      configuration = { inherit (configuration) top platform; };
      outputs.out = "netlist.v";
    }
    // tools.yosys
  );
  checkNetlist = stage {
    name = "check-netlist";
    transform = "netlist_check:check_synthesis";
    inputs.netlist = synth.artifacts.out;
    configuration = { inherit (configuration) top; };
    outputs.out = "netlist-check.json";
  };
  openroad =
    {
      name,
      inputs,
      outputs,
      needs ? [ ],
    }:
    stage (
      {
        inherit name outputs needs;
        transform = "physical:openroad";
        inputs = {
          "constraints.sdc" = constraints;
          "common.tcl" = scripts.common;
          "${name}.tcl" = scripts.${name};
        }
        // macroInputs ".lib" (views: views.liberty)
        // inputs;
        configuration = {
          inherit (configuration)
            top
            platform
            ;
          inherit macroCells;
          script = "${name}.tcl";
        }
        // lib.optionalAttrs (name == "floorplan") { inherit placements; };
      }
      // tools.openroad
    );

  floorplan = openroad {
    name = "floorplan";
    inputs = {
      "netlist.v" = synth.artifacts.out;
    }
    // macroInputs ".lef" (views: views.lef);
    outputs.out = "floorplan.odb";
    needs = lib.optional checkSynthesis checkNetlist.package;
  };
  place = openroad {
    name = "place";
    inputs."floorplan.odb" = floorplan.artifacts.out;
    outputs.out = "placed.odb";
  };
  cts = openroad {
    name = "cts";
    inputs."placed.odb" = place.artifacts.out;
    outputs.out = "cts.odb";
  };
  route = openroad {
    name = "route";
    inputs."cts.odb" = cts.artifacts.out;
    outputs = {
      out = "routed.odb";
      verilog = "routed.v";
      def = "final.def";
      spef = "parasitics.spef";
      lef = "abstract.lef";
      liberty = "timing.lib";
      timing = "timing.rpt";
      drc = "routing_drc.rpt";
    };
  };
  gds = stage {
    name = "gds";
    transform = "scripts.stream:stream";
    inputs = {
      "final.def" = route.artifacts.def;
    }
    // macroInputs ".lef" (views: views.lef)
    // macroInputs ".gds" (views: views.gds);
    configuration = {
      inherit (configuration) top platform;
      inherit macroCells;
    };
    pythonPackages = p: [ p.klayout ];
    outputs.out = "final.gds";
  };
in
{
  packages =
    lib.mapAttrs (_: step: step.package) {
      inherit
        synth
        floorplan
        place
        cts
        route
        gds
        ;
    }
    // lib.optionalAttrs checkSynthesis { check-netlist = checkNetlist.package; }
    // {
      netlist = synth.package;
      lef = lib.getOutput "lef" route.package;
      liberty = lib.getOutput "liberty" route.package;
      spef = lib.getOutput "spef" route.package;
    };
  views = {
    inherit (route.artifacts) lef liberty;
    gds = gds.artifacts.out;
  };
}
