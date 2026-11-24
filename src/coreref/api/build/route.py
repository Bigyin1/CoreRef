"""Declare and validate stage graphs for CoreRef builds.

The route controls stage dependencies and output names. The flow
provides actions and the files and configuration needed to run them.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..packages.records import JSON, Record

StageTransform = Callable[[Path, Path, Mapping[str, JSON]], None]


class StageSpec(BaseModel):
    """Describe a named stage before binding it to a handler.

    ``needs`` names direct predecessor stages. ``outputs`` lists relative
    paths produced by the stage. ``purpose`` determines whether the
    stage can participate in publication. During publication ``checks``
    adds validation stages.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: str = Field(pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_-]*$")
    outputs: tuple[str, ...] = Field(strict=False)
    needs: tuple[str, ...] = Field(default=(), strict=False)
    purpose: Literal["release", "development"] = "release"
    checks: tuple[str, ...] = Field(default=(), strict=False)


@dataclass(frozen=True, kw_only=True)
class Stage:
    """Bind a declared stage to a callable and its tracked inputs.

    A transform receives an isolated working directory, a log path and
    JSON configuration. CoreRef stages its declared inputs and checks
    its outputs before making them available. Use a module-level function
    for a Nix build. ``execution="relaxed"`` permits a configured Nix
    worker to relax its sandbox. ``execution="host"`` runs the same
    function with Nix Python under the caller's environment.

    If a transform omits ``inputs``, CoreRef uses its ``files`` and all
    outputs of direct predecessors. Explicit ``inputs`` select filenames
    from those sources or from predecessor outputs. The keys in ``files``
    are staged names and its values are source paths.

    ``configuration`` is passed to the transform
    and contributes to the build identity. Set ``always_run`` when a host
    transform must execute even if the executor can reuse its outputs.
    ``ready()`` checks stage-specific prerequisites. ``artifacts`` binds staged
    input names to named outputs from released packages.
    """

    transform: StageTransform
    execution: Literal["nix", "relaxed", "host"] = "nix"
    inputs: tuple[str, ...] | None = None
    files: Mapping[str, Path] = field(default_factory=dict)
    configuration: Mapping[str, JSON] = field(default_factory=dict)
    always_run: bool = False
    ready: Callable[[], bool] | None = None
    artifacts: Mapping[str, tuple[Record, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.execution not in {"nix", "relaxed", "host"}:
            raise ValueError(f"Unknown stage execution mode {self.execution!r}")
        overlap = self.files.keys() & self.artifacts.keys()
        if overlap:
            raise ValueError(f"Stage has two sources for {', '.join(sorted(overlap))}")
        for name in (*self.files, *self.artifacts, *(self.inputs or ())):
            _output_path(name)


class Route:
    """Validate the dependencies and output paths of a set of stages.

    ``order`` lists stage names in topological order with dependencies
    first. Look up a stage by name with indexing or check its membership
    before selecting it as a target.
    """

    def __init__(self, stages: list[StageSpec]) -> None:
        """Validate stage names, output paths and dependency edges.

        Reject duplicate output paths, missing stage references and cycles.
        """
        self._stages = {stage.name: stage for stage in stages}
        if len(stages) != len(self._stages):
            raise ValueError("Duplicate stage in flow")
        _validate_outputs(stages)
        for stage in stages:
            self._validate_needs(stage)
            self._validate_publication(stage)
        try:
            self.order = tuple(
                TopologicalSorter({stage.name: stage.needs for stage in stages}).static_order()
            )
        except CycleError as exc:
            raise ValueError(f"Cycle in flow stages: {exc.args[1]}") from exc

    def _validate_needs(self, stage: StageSpec) -> None:
        if len(stage.needs) != len(set(stage.needs)):
            raise ValueError(f"Duplicate dependencies of stage {stage.name!r}")
        for name in stage.needs:
            dependency = self[name]
            if stage.purpose == "release" and dependency.purpose == "development":
                raise ValueError(
                    f"Release stage {stage.name!r} depends on development stage {name!r}"
                )

    def _validate_publication(self, stage: StageSpec) -> None:
        for name in stage.checks:
            if self[name].purpose != "release":
                raise ValueError(f"Publication check {name!r} must be a release stage")

    def __getitem__(self, name: str) -> StageSpec:
        """Return the stage specification or raise ``ValueError`` for an unknown name."""
        try:
            return self._stages[name]
        except KeyError as exc:
            raise ValueError(f"Unknown stage {name!r}") from exc

    def __contains__(self, name: str) -> bool:
        """Check whether the route declares the named stage."""
        return name in self._stages

    def _ready(
        self, accepts: Callable[[StageSpec], bool], *, candidates: Collection[str] | None = None
    ) -> tuple[StageSpec, ...]:
        """Return release stages whose prerequisites and own inputs are available."""
        ready: dict[str, StageSpec] = {}
        for name in self.order:
            stage = self[name]
            if stage.purpose != "release" or (candidates is not None and name not in candidates):
                continue
            if all(name in ready for name in stage.needs) and accepts(stage):
                ready[stage.name] = stage
        return tuple(ready.values())

    @staticmethod
    def _targets(stages: tuple[StageSpec, ...]) -> tuple[str, ...]:
        """Find the terminal stages of a selected, dependency-closed subgraph."""
        upstream = {name for stage in stages for name in stage.needs}
        return tuple(stage.name for stage in stages if stage.name not in upstream)


def _validate_outputs(stages: list[StageSpec]) -> None:
    outputs: set[PurePosixPath] = set()
    for stage in stages:
        for name in stage.outputs:
            path = _output_path(name)
            if path in outputs:
                raise ValueError(f"Duplicate stage output {name!r}")
            outputs.add(path)
    for path in outputs:
        if outputs.intersection(path.parents):
            raise ValueError(f"Stage output overlaps another output: {path}")


def _output_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if not path.parts or path.is_absolute() or ".." in path.parts or str(path) != name:
        raise ValueError(f"Expected a relative stage output, got {name!r}")
    return path
