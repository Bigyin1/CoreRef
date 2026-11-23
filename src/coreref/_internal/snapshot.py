"""Collect and verify the inputs of a release snapshot."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ..api.packages.records import JSON, Ref
from .hashing import file_digest, fingerprint
from .producer import transform_identity
from .runtime import producer_files

if TYPE_CHECKING:
    from ..api.build.route import Route, Stage, StageSpec
    from ..api.packages.records import ConfiguredPackage
    from ..api.projects.project import Project


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


class Snapshot:
    """Capture a project slice from the declared inputs of its release stages."""

    def __init__(
        self,
        project: Project,
        route: Route,
        *,
        stage_inputs: Callable[[StageSpec], Stage],
        variants: Callable[[], Mapping[str, ConfiguredPackage]],
    ) -> None:
        self._project = project
        self._route = route
        self._stage_inputs = stage_inputs
        self._variants = variants

    def inputs(self, commit: str | None, outputs: Mapping[str, Path] | None = None) -> SliceInputs:
        """Read the complete release route independently of the current build target."""
        project = self._project
        stages = {stage.name: self._stage_inputs(stage) for stage in self._route._release()}
        files: dict[str, Path | None] = {
            f"packaged/{name}": path for name, path in project.record.files.items()
        }
        files.update(
            (f"stage/{stage}/{name}", path if path.is_file() else None)
            for stage, inputs in stages.items()
            for name, path in inputs.files.items()
        )
        if project._producer_handler is not None or any(
            stage.transform for stage in stages.values()
        ):
            files.update(
                (f"producer/{name}", path if path.is_file() else None)
                for name, path in producer_files(project.source).items()
            )
        return SliceInputs(
            project=project.record.ref,
            commit=commit,
            lockfile=project._require_lockfile(),
            dependencies=tuple(
                record.ref for record in project.graph.dependencies(transitive=True)
            ),
            environment={
                "manifest": project.manifest.model_dump(
                    mode="json", exclude={"ref", "dependencies", "files", "payload"}
                ),
                "route": self._route._release_document(),
                "stages": {
                    name: inputs.configuration
                    if inputs.transform is None
                    else {
                        "configuration": dict(inputs.configuration),
                        "transform": transform_identity(inputs.transform),
                        "inputs": inputs.inputs,
                        "artifacts": {
                            alias: (str(record.ref), output)
                            for alias, (record, output) in inputs.artifacts.items()
                        },
                    }
                    for name, inputs in stages.items()
                },
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
