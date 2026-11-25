# Select views and compose the routes of a hardware project.
{
  selectComponents,
  verilatorPkgs,
  edalize,
}:
{
  pkgs,
  system,
  mkStage,
}:
{
  name,
  source,
  profile,
  files ? { },
  parameters ? { },
  dependencies ? (_: [ ]),
  siteTools ? { },
}:
let
  inherit (pkgs) lib;
  stage =
    spec:
    mkStage (
      spec
      // {
        name = "${name}-${spec.name}";
        transform = "coreref_flow.stages.${spec.transform}";
      }
    );
  tool =
    key: packages:
    if siteTools ? ${key} then
      { siteTools = [ siteTools.${key} ]; }
    else
      { nativeBuildInputs = packages; };
  verilator =
    tool "verilator" [
      verilatorPkgs.legacyPackages.${system}.verilator
      pkgs.stdenv.cc
      pkgs.gnumake
    ]
    // {
      pythonPackages = p: [ (edalize p) ];
    };
  tcl = tool "tcl" [ pkgs.tcl ];

  roots = dependencies system;
  components = import ./components.nix {
    inherit
      lib
      name
      roots
      selectComponents
      ;
  };
  inherit (components) macro;

  hasFile =
    key: files ? ${key} && (!builtins.isPath files.${key} || builtins.pathExists files.${key});
  dftReady =
    profile != "rtl"
    && profile != "memory"
    && hasFile "dftScript"
    && (profile != "soc" || hasFile "dftReady");
  hasTestbench = hasFile "testbench";
  hasBackend = builtins.elem profile [
    "macro"
    "soc"
  ];
  configuration = {
    defines = [ ];
    macroInstance = "hard_macro";
    macro = if macro == null then "" else macro.parameters.top;
    platform = pkgs.fetchgit {
      name = "orfs-nangate45";
      url = "https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts.git";
      rev = "a9adcf481395d3b1355afda40df1c8bde10a0e40";
      rootDir = "flow/platforms/nangate45";
      fetchSubmodules = false;
      hash = "sha256-zGNtgSAEz96TXxUrmGQxM+7cXoIcihs2K5RT5mJ777U=";
    };
  }
  // parameters;
  sources = {
    inherit
      lib
      stage
      files
      hasTestbench
      ;
    inherit (components) rtlInputs all synthesizable;
  };
  rtl = import ./rtl.nix (
    sources
    // {
      inherit configuration verilator;
      inherit (components) abstracts;
    }
  );
  dft = import ./dft.nix (
    sources
    // {
      inherit tcl;
      inherit (rtl) simulate;
      hierarchical = profile == "dft";
    }
  );
  memory = import ./memory.nix {
    inherit
      stage
      files
      configuration
      verilator
      ;
  };
  physical = import ./physical.nix {
    inherit
      lib
      stage
      files
      macro
      configuration
      ;
    tools = {
      yosys = tool "yosys" [ pkgs.yosys ];
      openroad = tool "openroad" [ pkgs.openroad ];
    };
    checkSynthesis = profile == "soc";
    rtl = if dftReady then dft.artifact else rtl.forSynth.inline.out;
  };
in
assert
  builtins.elem profile [
    "rtl"
    "dft"
    "macro"
    "soc"
    "memory"
  ]
  || throw "unknown route profile '${profile}'";
{
  packages =
    if profile == "memory" then
      memory.packages
    else
      rtl.packages
      // lib.optionalAttrs dftReady dft.packages
      // lib.optionalAttrs hasBackend physical.packages;
  component = {
    inherit name;
    kind = if profile == "memory" then "rtl" else profile;
    id = builtins.toJSON [
      (toString source)
      parameters
    ];
    parameters = configuration;
    views =
      if profile == "memory" then
        memory.views
      else
        {
          rtl = files.rtl;
        }
        // lib.optionalAttrs dftReady dft.views
        // lib.optionalAttrs (profile == "macro") physical.views;
    dependencies = roots;
  };
}
