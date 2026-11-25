# Evaluate the actual fixture flakes without starting GitLab or any EDA tools.
let
  core = builtins.getFlake (toString ../..);
  inherit (core.inputs) nixpkgs;
  inherit (nixpkgs) lib;
  experimental = import ./flow/flake.nix;
  # Use the same release as the fixture flake while replacing its GitLab input.
  flowLock = builtins.fromJSON (builtins.readFile ./flow/flake.lock);
  flowInputs = {
    inherit nixpkgs;
    coreref = core;
    edalizeSrc = builtins.fetchTree flowLock.nodes.edalizeSrc.locked;
    verilatorPkgs = builtins.getFlake (builtins.flakeRefToString flowLock.nodes.verilatorPkgs.locked);
  };
  flow = experimental.outputs flowInputs;
  project =
    name: args:
    let
      source = ./projects + "/${name}";
    in
    (import (source + "/flake.nix")).outputs (
      {
        self.outPath = source;
        inherit flow nixpkgs;
      }
      // args
    );
  increment = project "increment" { };
  alternateRtl = increment.lib.mkRtl { step = 2; };
  counter = project "counter" { inherit increment; };
  status = project "status-register" { inherit increment; };
  sram = project "sram" { };
  withFiles = overrides: {
    lib.mkProject = config: flow.lib.mkProject (config // { files = config.files // overrides; });
  };
  missingScript = ./missing-dft-script;
  statusWithoutDft = project "status-register" {
    inherit increment;
    flow = withFiles { dftScript = missingScript; };
  };
  socWith =
    overrides:
    project "soc" {
      inherit counter sram;
      status-register = status;
      flow = withFiles overrides;
    };
  soc = socWith { };

  system = "aarch64-linux";
  ready = soc.packages.${system};
  failingScript = builtins.toFile "broken-dft.tcl" ''error "DFT fixture error"'';
  fallback = socWith {
    dftReady = ./missing-dft-marker;
    dftScript = failingScript;
  };
  withoutScript = socWith { dftScript = missingScript; };
  broken = socWith { dftScript = failingScript; };
  withoutDft = map (projectOutputs: projectOutputs.packages.${system}) [
    fallback
    withoutScript
  ];
  changed = (socWith { place = ./variants/place.tcl; }).packages.${system};
  changedEdalize =
    (project "soc" {
      inherit counter sram;
      status-register = status;
      flow = experimental.outputs (
        flowInputs
        // {
          # Evaluation only. A different source must affect the verification environments.
          edalizeSrc = builtins.toFile "different-edalize-source" "test source";
        }
      );
    }).packages.${system};
  memory = sram.packages.${system};
  plan = stage: builtins.fromJSON (builtins.unsafeDiscardStringContext stage.stagePlan);
  synth = (plan ready.synth).inputs.rtl;
  lintFiles = (plan ready.lint-physical).inputs;
  inlineRtl = (plan ready.simulate).inputs.rtl.plan;
  dftFiles = (plan ready.dft).inputs;
  first =
    (sram.lib.mkSram {
      depth = 4;
      width = 4;
    }).lib.componentFor
      system;
  custom = sram.lib.mkSram {
    depth = 5;
    width = 3;
  };
  invalid = sram.lib.mkSram {
    depth = 1;
    width = 4;
  };
in
{
  check =
    assert builtins.attrNames soc.apps.${system} == [ "flow" ];
    assert lib.isDerivation soc.devShells.${system}.default;
    assert lib.hasSuffix "-soc-dft/hdl" synth;
    assert lib.hasSuffix "-run.tcl" dftFiles.script;
    assert
      builtins.attrNames (plan ready.simulate).inputs == [
        "rtl"
        "testbench"
      ];
    assert inlineRtl.name == "soc-rtl";
    assert inlineRtl.outputs.out == "hdl";
    assert
      !(builtins.hasAttr (builtins.unsafeDiscardStringContext ready.rtl.drvPath) (
        builtins.getContext ready.simulate.stagePlan
      ));
    assert lib.hasSuffix "-soc-dft-simulation/hdl" (plan ready.simulate-dft).inputs.rtl;
    assert lib.hasSuffix "-soc-dft/hdl" (plan broken.packages.${system}.synth).inputs.rtl;
    assert lib.all (
      packages: (plan packages.synth).inputs.rtl.plan.name == "soc-rtl-synthesis-input"
    ) withoutDft;
    assert lib.all (
      packages: !(packages ? dft) && !(packages ? simulate-dft) && packages ? simulate
    ) withoutDft;
    assert !((statusWithoutDft.lib.componentFor system).views ? dft);
    assert lib.hasSuffix "-soc-check-netlist" (builtins.head (plan ready.floorplan).orderOnly);
    assert lib.all
      (output: ready.${output}.outputName == output && ready.${output}.drvPath == ready.route.drvPath)
      [
        "lef"
        "liberty"
        "spef"
      ];
    assert
      builtins.attrNames lintFiles == [
        "macros/counter/abstract.lef"
        "macros/counter/timing.lib"
        "macros/sram-4x4/abstract.lef"
        "macros/sram-4x4/timing.lib"
        "macros/sram-8x8/abstract.lef"
        "macros/sram-8x8/timing.lib"
        "rtl"
      ];
    assert
      (builtins.filter (lib.hasPrefix "sources/") (builtins.attrNames lintFiles.rtl.plan.inputs)) == [
        "sources/000000-increment/000000/step_one.v"
        "sources/000000-increment/000001/increment.v"
        "sources/000001-status-register/000000/rtl.v"
      ];
    assert lib.hasSuffix "/dft_module.v" dftFiles."sources/000001-status-register";
    assert builtins.any (lib.hasInfix "-counter/") (builtins.attrNames inlineRtl.inputs);
    assert
      builtins.attrNames ((plan alternateRtl.packages.${system}.simulate).inputs.rtl.plan.inputs) == [
        "files/000000/step_two.v"
        "files/000001/increment.v"
      ];
    assert (plan alternateRtl.packages.${system}.simulate).configuration.defines == [ "TB_STEP=2" ];
    assert
      map builtins.baseNameOf (alternateRtl.lib.componentFor system).views.rtl == [
        "step_two.v"
        "increment.v"
      ];
    assert first.views.rtl == "${memory."4x4"}/hdl";
    assert memory."4x4".drvPath != memory."8x8".drvPath;
    assert
      (plan memory."simulate-4x4").configuration == {
        depth = 4;
        width = 4;
        top = "sram_4x4";
      };
    assert (custom.lib.componentFor system).views.rtl == "${custom.packages.${system}.rtl}/hdl";
    assert
      (plan custom.packages.${system}.simulate).configuration == {
        depth = 5;
        width = 3;
        top = "sram_5x3";
      };
    assert !(builtins.tryEval invalid).success;
    assert inlineRtl.configuration.defines == [ "INCREMENT_ONE" ];
    assert (plan ready.dft).configuration.defines == [ "INCREMENT_ONE" ];
    assert (plan ready.simulate).configuration.defines == [ ];
    assert (plan ready.simulate-dft).configuration.defines == [ "COREREF_DFT" ];
    assert
      builtins.attrNames (plan ready.gds).inputs == [
        "final.def"
        "macros/counter.gds"
        "macros/counter.lef"
        "macros/sram-4x4.gds"
        "macros/sram-4x4.lef"
        "macros/sram-8x8.gds"
        "macros/sram-8x8.lef"
      ];
    assert lib.all (target: ready.${target}.drvPath == changedEdalize.${target}.drvPath) [
      "rtl"
      "dft"
      "netlist"
      "gds"
    ];
    assert lib.all (target: ready.${target}.drvPath != changedEdalize.${target}.drvPath) [
      "lint"
      "simulate"
      "simulate-dft"
    ];
    assert lib.all (target: ready.${target}.drvPath == changed.${target}.drvPath) [
      "rtl"
      "dft"
      "netlist"
      "floorplan"
    ];
    assert lib.all (target: ready.${target}.drvPath != changed.${target}.drvPath) [
      "place"
      "cts"
      "route"
      "gds"
    ];
    true;

  inherit (soc.packages.${builtins.currentSystem}) rtl dft;
  inherit (soc.packages.${builtins.currentSystem})
    simulate
    simulate-dft
    lint-physical
    netlist
    gds
    ;
  incrementSimulation = increment.packages.${builtins.currentSystem}.simulate;
  incrementLint = increment.packages.${builtins.currentSystem}.lint;
  customMemorySimulation = custom.packages.${builtins.currentSystem}.simulate;
  alternateRtlSimulation = alternateRtl.packages.${builtins.currentSystem}.simulate;
  fallbackNetlist = fallback.packages.${builtins.currentSystem}.netlist;
  customMemory = custom.packages.${builtins.currentSystem}.rtl;
  fallbackRtl = fallback.packages.${builtins.currentSystem}.rtl-synthesis-input;
  brokenDft = broken.packages.${builtins.currentSystem}.dft;
}
