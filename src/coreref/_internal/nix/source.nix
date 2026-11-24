# Import tracked sources or a Python package with a stable name.
{
  directory,
  name,
  selection ? "null",
}:
let
  tracked = builtins.fromJSON selection;
in
{
  source = builtins.path {
    path = /. + directory;
    inherit name;
    filter =
      path: _:
      (tracked == null || builtins.hasAttr path tracked)
      && !(builtins.elem (builtins.baseNameOf path) [
        ".git"
        ".build"
        ".venv"
        ".env"
        ".direnv"
        ".DS_Store"
        "__pycache__"
        ".pytest_cache"
        ".mypy_cache"
        ".ruff_cache"
      ]);
  };
}
