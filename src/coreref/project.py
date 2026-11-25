"""Python operations for CLI commands that work with a project flake."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ._internal import workspace
from ._internal.flake import FlakeLock, input_node, root_node


@dataclass(frozen=True)
class Dependency:
    """One direct input, its locked revision, and any local override."""

    name: str
    revision: str | None
    editable: Path | None


class Project:
    """Access a flake by directory or reference and manage its local workspace."""

    def __init__(self, flake: str | Path = ".") -> None:
        directory = Path(flake)
        self._directory = directory.resolve() if directory.is_dir() else None
        self._reference = str(self._directory or flake)

    def build(self, *targets: str, options: Sequence[str] = ()) -> tuple[Path, ...]:
        """Build the selected targets with the workspace inputs and return their store paths."""
        if not targets or any(not target or "#" in target for target in targets):
            raise ValueError("Provide at least one flake target name")
        installables = [f"{self._reference}#{target}" for target in targets]
        outputs = _run(
            "nix",
            "build",
            *installables,
            "--print-out-paths",
            *workspace.arguments(self.editables()),
            *options,
        )
        return tuple(Path(path) for path in outputs.splitlines())

    def dependencies(self) -> tuple[Dependency, ...]:
        """List the direct inputs in flake.lock, with workspace changes shown alongside."""
        lock = self._lock()
        checkouts = self.editables()
        return tuple(
            Dependency(name, _revision(lock, name), checkouts.get(name))
            for name in sorted(root_node(lock).get("inputs", {}))
        )

    def editables(self) -> dict[str, Path]:
        """Show the local checkout paths set on this project."""
        return workspace.read(self._directory) if self._directory is not None else {}

    def add_editable(self, name: str, directory: str | Path) -> Path:
        """Use an existing directory for a declared flake input."""
        project = self._local_project()
        node = input_node(self._lock(), name)
        checkout = Path(directory).resolve()
        _check_directory(checkout, flake=node.get("flake", True))
        workspace.update(project, name, checkout)
        return checkout

    def remove_editable(self, name: str) -> bool:
        """Restore the locked dependency and report whether a substitution existed."""
        return workspace.update(self._local_project(), name, None)

    def checkout(self, name: str, destination: str | Path) -> Path:
        """Clone a Git input at its locked revision and add the checkout to the workspace."""
        project = self._local_project()
        node = input_node(self._lock(), name)
        source = node.get("locked", {})
        revision = source.get("rev")
        if revision is None or source.get("type") not in {"git", "github", "gitlab", "sourcehut"}:
            raise ValueError(f"Input {name!r} has no locked Git revision to clone")
        destination = Path(destination).resolve()
        # Nix 2.33 can clone the repository, while Git selects the locked commit.
        remote = {
            key: value for key, value in source.items() if key not in {"ref", "rev", "narHash"}
        }
        _run("nix", "flake", "clone", _reference(remote), "--dest", str(destination))
        _run("git", "-C", str(destination), "switch", "--detach", "--", str(revision))
        directory = destination / str(source.get("dir", ""))
        _check_directory(directory, flake=node.get("flake", True))
        workspace.update(project, name, directory)
        return directory

    def _lock(self) -> FlakeLock:
        if self._directory is not None:
            lock_file = self._directory / "flake.lock"
            if lock_file.is_file():
                return cast(FlakeLock, json.loads(lock_file.read_text(encoding="utf-8")))
        metadata = json.loads(
            _run("nix", "flake", "metadata", self._reference, "--json", "--no-write-lock-file")
        )
        return cast(FlakeLock, metadata["locks"])

    def _local_project(self) -> Path:
        if self._directory is None or not (self._directory / "flake.nix").is_file():
            raise ValueError("Workspace commands require a local project directory with flake.nix")
        return self._directory


def _revision(lock: FlakeLock, name: str) -> str | None:
    revision = input_node(lock, name).get("locked", {}).get("rev")
    return str(revision) if revision is not None else None


def _check_directory(directory: Path, *, flake: bool) -> None:
    if not directory.is_dir():
        raise ValueError(f"Expected a checkout directory at {directory}")
    if flake and not (directory / "flake.nix").is_file():
        raise ValueError(f"Expected flake.nix in {directory}")


def _reference(attributes: dict[str, str | int | bool]) -> str:
    return _run(
        "nix",
        "--store",
        "dummy://",
        "eval",
        "--raw",
        "--impure",
        "--expr",
        "builtins.flakeRefToString (builtins.fromJSON (builtins.readFile /dev/stdin))",
        data=json.dumps(attributes),
    )


def _run(program: str, *arguments: str, data: str | None = None) -> str:
    executable = shutil.which(program)
    if executable is None:
        raise FileNotFoundError(f"{program} executable not found in PATH")
    result = subprocess.run(  # noqa: S603 - invoke Nix or Git directly with an argument list.
        [executable, *arguments], input=data, stdout=subprocess.PIPE, text=True, check=True
    )
    return result.stdout
