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
    let
      inherit (nixpkgs) lib;
      inherit (lib) fileset;
      forSystems = lib.genAttrs [
        "aarch64-darwin"
        "aarch64-linux"
        "x86_64-linux"
      ];
      withoutBytecode = fileset.fileFilter (file: !file.hasExt "pyc");
      stageSource = fileset.toSource {
        root = ./src/coreref_flow;
        fileset = fileset.unions [
          ./src/coreref_flow/__init__.py
          (withoutBytecode ./src/coreref_flow/stages)
        ];
      };
      cliSource = fileset.toSource {
        root = ./.;
        fileset = fileset.unions [
          ./pyproject.toml
          ./README.md
          (withoutBytecode ./src)
        ];
      };
      metadata = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project;
      edalize =
        p:
        p.buildPythonPackage {
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
      bySystem = forSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          corerefPackage = coreref.packages.${system}.default;
          stageModules = pkgs.python3Packages.toPythonModule (
            pkgs.runCommand "coreref-flow-stages" { } ''
              mkdir -p "$out/${pkgs.python3.sitePackages}/coreref_flow"
              cp -R ${stageSource}/. "$out/${pkgs.python3.sitePackages}/coreref_flow/"
            ''
          );
          python =
            extra:
            pkgs.python3.withPackages (
              p:
              [
                corerefPackage
                stageModules
              ]
              ++ extra p
            );
          cli = pkgs.python3Packages.buildPythonPackage {
            pname = metadata.name;
            inherit (metadata) version;
            pyproject = true;
            src = cliSource;
            build-system = [ pkgs.python3Packages.hatchling ];
            dependencies = [
              corerefPackage
              pkgs.python3Packages.typer
            ];
            pythonImportsCheck = [ "coreref_flow.cli" ];
          };
        in
        {
          inherit cli;
          project = import ./nix/project.nix { inherit verilatorPkgs edalize; } {
            inherit pkgs system;
            mkStage = coreref.lib.mkStage { inherit pkgs python; };
          };
          shell = pkgs.mkShell {
            packages = [ (pkgs.python3.withPackages (_: [ cli ])) ];
          };
        }
      );
      apps = lib.mapAttrs (_: flow: {
        flow = {
          type = "app";
          program = "${flow.cli}/bin/coreref-flow";
        };
      }) bySystem;
      devShells = lib.mapAttrs (_: flow: { default = flow.shell; }) bySystem;
    in
    {
      inherit apps devShells;
      packages = lib.mapAttrs (_: flow: { default = flow.cli; }) bySystem;
      lib.mkProject =
        config:
        let
          projects = lib.mapAttrs (_: flow: flow.project config) bySystem;
        in
        {
          inherit apps devShells;
          packages = lib.mapAttrs (_: project: project.packages) projects;
          lib.componentFor = system: projects.${system}.component;
        };
    };
}
