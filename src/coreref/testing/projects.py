"""Copy project fixtures while keeping symlinks inside the owned working tree."""

import os
from pathlib import Path
from shutil import copytree, ignore_patterns


def copy_projects(source: Path, destination: Path) -> None:
    """Rebase internal absolute links and reject external links before copying."""
    absolute_links: dict[Path, Path] = {}
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if ".git" in relative.parts or not path.is_symlink():
            continue
        target = Path(os.path.abspath(path.parent / path.readlink()))
        if not target.is_relative_to(source) or not path.resolve().is_relative_to(source):
            raise ValueError(f"Project symlink points outside the source tree: {relative}")
        if path.readlink().is_absolute():
            absolute_links[relative] = target.relative_to(source)
    copytree(source, destination, symlinks=True, ignore=ignore_patterns(".git"))
    for relative, target in absolute_links.items():
        link = destination / relative
        link.unlink()
        link.symlink_to(
            os.path.relpath(destination / target, link.parent),
            target_is_directory=(source / target).is_dir(),
        )
