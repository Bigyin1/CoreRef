{
  description = "CoreRef RTL, hierarchical DFT and OpenROAD flow";
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/94c3211b0ee8ba969a3f12eb4aff8ba0f72971dd";
    # This revision carries the Verilator build that also works on Darwin.
    verilatorPkgs.url = "github:NixOS/nixpkgs/73e728ddb6b7a12d18808f510813a13ee1fe4cce";
    edalizeSrc = {
      url = "https://files.pythonhosted.org/packages/source/e/edalize/edalize-0.6.7.tar.gz";
      flake = false;
    };
    coreref = {
      url = "git+http://gitlab:18081/root/coreref.git?ref=fixture";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      nixpkgs,
      verilatorPkgs,
      edalizeSrc,
      coreref,
      ...
    }:
    coreref.lib.mkFlow {
      inherit nixpkgs;
      source = ./.;
      project = import ./nix/project.nix {
        inherit verilatorPkgs;
        inherit (coreref.lib) selectComponents;
      };
      cli = "coreref-flow";
      pythonDependencies =
        p:
        let
          edalize = p.buildPythonPackage {
            pname = "edalize";
            version = "0.6.7";
            pyproject = true;
            src = edalizeSrc;
            build-system = [
              p.setuptools
              p.setuptools-scm
            ];
            dependencies = [ p.jinja2 ];
            pythonImportsCheck = [
              "edalize.flows.lint"
              "edalize.flows.sim"
            ];
          };
        in
        [
          p.typer
          edalize
        ];
    };
}
