{
  inputs = {
    flow.url = "git+http://gitlab:18081/root/coreref-flow.git?ref=fixture";
    nixpkgs.follows = "flow/nixpkgs";
    increment = {
      url = "git+http://gitlab:18081/root/increment.git?ref=fixture";
      inputs.flow.follows = "flow";
    };
  };

  outputs =
    {
      self,
      flow,
      increment,
      nixpkgs,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      mkCounter =
        step:
        flow.lib.mkProject {
          name = "counter";
          source = self.outPath;
          profile = "macro";
          parameters = {
            inherit step;
            top = "counter";
            defines = [ "COUNTER_RESET=0" ];
            verificationDefines = [ "TB_COUNTER_STEP=${toString step}" ];
          };
          rtl = {
            sources = [ ./rtl/rtl.v ];
            testbench = ./rtl/testbench.sv;
          };
          dft = {
            enabled = true;
            script = ./dft/run.tcl;
          };
          backend.constraints = ./be/constraints.sdc;
          dependencies = system: [ ((increment.lib.mkRtl { inherit step; }).lib.componentFor system) ];
        };
      variants = {
        plus-1 = mkCounter 1;
        plus-3 = mkCounter 3;
      };
      default = variants.plus-1;
      exported = [
        "rtl"
        "simulate"
        "netlist"
        "lef"
        "liberty"
        "gds"
      ];
      targets =
        system:
        lib.mergeAttrsList (
          lib.mapAttrsToList (
            variant: project:
            builtins.listToAttrs (
              map (target: lib.nameValuePair "${variant}-${target}" project.packages.${system}.${target}) exported
            )
          ) variants
        );
    in
    default
    // {
      packages = lib.mapAttrs (system: packages: packages // targets system) default.packages;
      lib = default.lib // {
        inherit variants;
      };
    };
}
