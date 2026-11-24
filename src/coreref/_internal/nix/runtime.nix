# Use one pinned Nixpkgs Python environment for CoreRef and its stage functions.
let
  pkgs = (builtins.getFlake "github:NixOS/nixpkgs/94c3211b0ee8ba969a3f12eb4aff8ba0f72971dd").legacyPackages.${builtins.currentSystem};
in
pkgs.python313.withPackages (ps: with ps; [
  frozendict
  jinja2
  packaging
  pydantic
  pyyaml
  typer
  typing-extensions
])
