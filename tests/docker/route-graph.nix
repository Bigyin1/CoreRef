# Evaluate the actual fixture flakes without starting GitLab or any EDA tools.
let
  core = builtins.getFlake (toString ../..);
  inherit (core.inputs) nixpkgs;
  inherit (nixpkgs) lib;
  experimental = import ./flow/flake.nix;
  flow = experimental.outputs {
    inherit nixpkgs;
    coreref = core;
    verilatorPkgs = builtins.getFlake experimental.inputs.verilatorPkgs.url;
  };
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
      inherit counter increment sram;
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
  memory = sram.packages.${system};
  plan = stage: builtins.fromJSON (builtins.unsafeDiscardStringContext stage.stagePlan);
  lintFiles = (plan ready.lint-physical).inputs;
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
    assert lib.hasSuffix "/dft_output.v" (plan ready.synth).inputs.rtl;
    assert lib.hasSuffix "-run.tcl" dftFiles.script;
    assert
      builtins.attrNames (plan ready.simulate).inputs == [
        "rtl"
        "testbench"
      ];
    assert lib.hasSuffix "-soc-rtl/rtl.v" (plan ready.simulate).inputs.rtl;
    assert lib.hasSuffix "-soc-dft-simulation/dft_output.v" (plan ready.simulate-dft).inputs.rtl;
    assert lib.hasSuffix "/dft_output.v" (plan broken.packages.${system}.synth).inputs.rtl;
    assert lib.all (
      packages: lib.hasSuffix "-soc-rtl-synthesis-input/rtl.v" (plan packages.synth).inputs.rtl
    ) withoutDft;
    assert lib.all (
      packages: !(packages ? dft) && !(packages ? simulate-dft) && packages ? simulate
    ) withoutDft;
    assert !((statusWithoutDft.lib.componentFor system).views ? dft);
    assert lib.hasSuffix "-soc-check-netlist" (builtins.head (plan ready.floorplan).orderOnly);
    assert
      builtins.attrNames lintFiles == [
        "macros/counter/abstract.lef"
        "macros/counter/timing.lib"
        "rtl"
        "sources/increment.v"
        "sources/sram-4x4.v"
        "sources/sram-8x8.v"
        "sources/status-register.v"
      ];
    assert lib.hasSuffix "/dft_module.v" dftFiles."sources/status-register.v";
    assert builtins.hasAttr "sources/counter.v" (plan ready.rtl).inputs;
    assert first.views.rtl == "${memory."4x4"}/rtl.v";
    assert memory."4x4".drvPath != memory."8x8".drvPath;
    assert
      (plan memory."simulate-4x4").configuration == {
        depth = 4;
        width = 4;
        top = "sram_4x4";
      };
    assert (custom.lib.componentFor system).views.rtl == "${custom.packages.${system}.rtl}/rtl.v";
    assert
      (plan custom.packages.${system}.simulate).configuration == {
        depth = 5;
        width = 3;
        top = "sram_5x3";
      };
    assert !(builtins.tryEval invalid).success;
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
  customMemory = custom.packages.${builtins.currentSystem}.rtl;
  fallbackRtl = fallback.packages.${builtins.currentSystem}.rtl-synthesis-input;
  brokenDft = broken.packages.${builtins.currentSystem}.dft;
}
