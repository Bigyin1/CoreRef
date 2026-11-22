"""Collect and verify the inputs of a release snapshot."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from ..api.build.outputs import SliceInputs
from ..api.packages.records import JSON

if TYPE_CHECKING:
    from ..api.build.route import Route, StageInputs, StageSpec
    from ..api.packages.variants import ConfiguredPackage
    from ..api.projects.project import Project


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
        stage_inputs: Callable[[StageSpec], StageInputs],
        variants: Callable[[], Mapping[str, ConfiguredPackage]],
    ) -> None:
        self._project = project
        self._route = route
        self._stage_inputs = stage_inputs
        self._variants = variants

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
