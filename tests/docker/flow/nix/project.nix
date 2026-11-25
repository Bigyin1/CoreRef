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
  rtl ? { },
  dft ? { },
  backend ? { },
  memory ? { },
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
      roots
      selectComponents
      ;
  };
  inherit (components) macros;
  hasFile = file: file != null && (!builtins.isPath file || builtins.pathExists file);
  dftReady =
    (dft.enabled or false) && profile != "rtl" && profile != "memory" && hasFile (dft.script or null);
  testbench = rtl.testbench or null;
  hasTestbench = hasFile testbench;
  hasBackend = builtins.elem profile [
    "macro"
    "soc"
  ];
  configuration = {
    defines = [ ];
    verificationDefines = [ ];
    macroPlacement = { };
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
  rtlArgs = {
    inherit
      lib
      stage
      hasTestbench
      ;
    sourceFiles = rtl.sources;
    inherit (components) rtlInputs all synthesizable;
  };
  rtlRoute = import ./rtl.nix (
    rtlArgs
    // {
      inherit configuration verilator testbench;
      inherit (components) abstracts;
    }
  );
  dftRoute = import ./dft.nix (
    rtlArgs
    // {
      inherit tcl;
      inherit (configuration) defines;
      inherit (rtlRoute) simulate;
      inherit (dft) script;
      hierarchical = profile == "dft";
    }
  );
  memoryRoute = import ./memory.nix {
    inherit
      lib
      stage
      configuration
      verilator
      ;
    inherit (memory) template testbench;
    body = memory.body or null;
  };
  physical = import ./physical.nix {
    inherit
      lib
      stage
      macros
      configuration
      ;
    inherit (backend) constraints;
    scriptOverrides = backend.scripts or { };
    tools = {
      yosys = tool "yosys" [ pkgs.yosys ];
      openroad = tool "openroad" [ pkgs.openroad ];
    };
    checkSynthesis = profile == "soc";
    rtl =
      if profile == "memory" then
        memoryRoute.views.rtl
      else if dftReady then
        dftRoute.artifact
      else
        rtlRoute.forSynth.inline.out;
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
      memoryRoute.packages // physical.packages
    else
      rtlRoute.packages
      // lib.optionalAttrs dftReady dftRoute.packages
      // lib.optionalAttrs hasBackend physical.packages;
  component = {
    inherit name;
    kind = if profile == "memory" then "macro" else profile;
    id = builtins.toJSON [
      (toString source)
      parameters
    ];
    parameters = configuration;
    views =
      if profile == "memory" then
        memoryRoute.views // physical.views
      else
        {
          rtl = rtl.sources;
        }
        // lib.optionalAttrs (profile == "macro" && (rtl.blackbox or null) != null) {
          inherit (rtl) blackbox;
        }
        // lib.optionalAttrs dftReady dftRoute.views
        // lib.optionalAttrs (profile == "macro") physical.views;
    dependencies = roots;
  };
}
