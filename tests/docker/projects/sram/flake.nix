{
  inputs = {
    flow.url = "git+http://gitlab:18081/root/coreref-flow.git?ref=fixture";
    nixpkgs.follows = "flow/nixpkgs";
  };

  outputs =
    {
      self,
      flow,
      nixpkgs,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      mkSram =
        { depth, width }:
        assert builtins.isInt depth && depth >= 2 || throw "sram: depth must be an integer >= 2";
        assert builtins.isInt width && width >= 1 || throw "sram: width must be an integer >= 1";
        let
          size = "${toString depth}x${toString width}";
        in
        flow.lib.mkProject {
          name = "sram-${size}";
          source = self.outPath;
          profile = "memory";
          memory = {
            template = ./memory.v.in;
            testbench = ./testbench.sv;
          };
          backend.constraints = ./constraints.sdc;
          parameters = {
            inherit depth width;
            top = "sram_${size}";
          };
        };
      presets = {
        "4x4" = mkSram {
          depth = 4;
          width = 4;
        };
        "4x8" = mkSram {
          depth = 4;
          width = 8;
        };
        "8x4" = mkSram {
          depth = 8;
          width = 4;
        };
        "8x8" = mkSram {
          depth = 8;
          width = 8;
        };
      };
      default = presets."4x4";
      targets =
        system:
        lib.mergeAttrsList (
          lib.mapAttrsToList (
            size: project:
            {
              ${size} = project.packages.${system}.rtl;
              "simulate-${size}" = project.packages.${system}.simulate;
            }
            // lib.genAttrs (map (view: "${size}-${view}") [
              "lef"
              "liberty"
              "gds"
            ]) (target: project.packages.${system}.${lib.removePrefix "${size}-" target})
          ) presets
        );
    in
    default
    // {
      packages = lib.mapAttrs (system: packages: packages // targets system) default.packages;
      lib = default.lib // {
        inherit mkSram;
      };
    };
}
