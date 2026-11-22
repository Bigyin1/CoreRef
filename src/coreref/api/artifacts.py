"""Inputs that identify a project slice and its contents."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .records import JSON, Ref

if TYPE_CHECKING:
    from .project import Project
    from .route import Route, StageSpec
    from .variants import ConfiguredPackage


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(document: JSON) -> str:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _git(source: Path, *arguments: str) -> str:
    return subprocess.check_output(  # noqa: S603 - fixed Git commands, paths are passed as arguments.
        ["git", "-C", str(source), *arguments],  # noqa: S607 - use the project's Git executable.
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


def clean_commit(source: Path) -> str | None:
    """Return HEAD for a clean Git worktree rooted at source."""
    source = source.resolve()
    try:
        if Path(_git(source, "rev-parse", "--show-toplevel")).resolve() != source:
            return None
        if _git(source, "status", "--porcelain", "--untracked-files=all"):
            return None
        return _git(source, "rev-parse", "HEAD")
    except subprocess.CalledProcessError:
        return None


@contextmanager
def committed_slice(source: Path, capture: Callable[[str], SliceInputs]) -> Iterator[SliceInputs]:
    """Check that a build used unchanged inputs from a clean Git checkout."""
    commit = clean_commit(source)
    if commit is None:
        raise ValueError("Publishing a project slice requires a clean Git checkout")
    inputs = capture(commit)
    key = inputs.key()
    yield inputs
    if clean_commit(source) != commit or capture(commit).key() != key:
        raise ValueError("Project inputs changed while building the slice")


@dataclass(frozen=True)
class SliceInputs:
    """Inputs that identify a project slice independently of the requested stage."""

    project: Ref
    commit: str | None
    lockfile: Path
    dependencies: tuple[Ref, ...]
    environment: JSON
    variants: Mapping[str, str] = field(default_factory=dict)
    local_outputs: Mapping[str, Path] = field(default_factory=dict)
    input_files: Mapping[str, Path | None] | None = None

    def key(self) -> str:
        """Hash the resolved inputs, including output bytes for an unpublished worktree."""
        local_outputs = self.local_outputs if self.commit is None else {}
        inputs: dict[str, JSON] = {
            "project": str(self.project),
            "local_outputs": {name: file_digest(path) for name, path in local_outputs.items()},
            "lockfile": file_digest(self.lockfile),
            "dependencies": tuple(sorted(map(str, self.dependencies))),
            "variants": self.variants,
            "environment": self.environment,
        }
        if self.input_files is None:
            return fingerprint({"schema_version": 1, "commit": self.commit, **inputs})
        files = _input_digests(self.input_files)
        return fingerprint(
            {"schema_version": 2, "worktree": self.commit is None, "files": files, **inputs}
        )


def _input_digests(files: Mapping[str, Path | None]) -> dict[str, str | None]:
    digests = {path: file_digest(path) for path in set(files.values()) if path is not None}
    return {name: digests[path] if path is not None else None for name, path in files.items()}


@dataclass(frozen=True)
class StageInputs:
    """External files and configuration that identify a stage before its outputs exist."""

    files: Mapping[str, Path] = field(default_factory=dict)
    configuration: JSON = None


class Snapshot:
    """Capture a project slice from the declared inputs of its release stages."""

    def __init__(
        self,
        project: Project,
        route: Route,
        *,
        stage_inputs: Callable[[StageSpec], StageInputs],
        variants: Callable[[], Mapping[str, ConfiguredPackage]] | None = None,
    ) -> None:
        self._project = project
        self._route = route
        self._stage_inputs = stage_inputs
        self._variants = variants if variants is not None else project.install_variants

    def inputs(self, commit: str | None, outputs: Mapping[str, Path] | None = None) -> SliceInputs:
        """Read the complete release route independently of the current build target."""
        project = self._project
        stages = {stage.name: self._stage_inputs(stage) for stage in self._route.release()}
        files: dict[str, Path | None] = {
            f"packaged/{name}": path for name, path in project.record.files.items()
        }
        files.update(
            (f"stage/{stage}/{name}", path if path.is_file() else None)
            for stage, inputs in stages.items()
            for name, path in inputs.files.items()
        )
        return SliceInputs(
            project=project.record.ref,
            commit=commit,
            lockfile=project.require_lockfile(),
            dependencies=tuple(record.ref for record in project.dependencies),
            environment={
                "manifest": project.manifest.model_dump(
                    mode="json", exclude={"ref", "dependencies", "files", "payload"}
                ),
                "route": self._route.release_document(),
                "stages": {name: inputs.configuration for name, inputs in stages.items()},
            },
            variants={name: package.binary_reference for name, package in self._variants().items()},
            local_outputs=outputs or {},
            input_files=files,
        )

    def metadata(self, inputs: SliceInputs) -> dict[str, JSON]:
        """Return project provenance for the output index."""
        return {
            "project": str(inputs.project),
            "commit": inputs.commit,
            "tools": self._project.manifest.model_dump(mode="json")["tools"],
        }
