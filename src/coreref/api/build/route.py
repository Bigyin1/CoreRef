"""Declare and validate stage graphs for CoreRef builds.

The route controls stage dependencies and output names. The flow
provides actions and the files and configuration needed to run them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
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
    paths within the build directory. ``purpose`` determines whether the
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
    from those sources or from the build directory. The keys in ``files``
    are staged names and its values are source paths.

    ``action`` is a local callback called with no arguments. It uses the
    build directory directly. ``configuration`` contributes to both local
    execution and release inputs, while ``local_configuration`` applies
    only to local runs. Set ``always_run`` when the action must execute
    even if the host executor can reuse its outputs.
    ``ready()`` checks stage-specific prerequisites. A shared
    ``prepare(stages)`` callback can compute inputs for selected stages in one
    operation. ``artifacts`` binds staged input names to named outputs from
    released packages. CoreRef retrieves only the named artifacts.
    """

    action: Callable[[], object] | None = None
    transform: StageTransform | None = None
    execution: Literal["nix", "relaxed", "host"] = "nix"
    inputs: tuple[str, ...] | None = None
    files: Mapping[str, Path] = field(default_factory=dict)
    configuration: Mapping[str, JSON] = field(default_factory=dict)
    local_configuration: Mapping[str, JSON] = field(default_factory=dict)
    always_run: bool = False
    ready: Callable[[], bool] | None = None
    prepare: Callable[[tuple[StageSpec, ...]], None] | None = None
    artifacts: Mapping[str, tuple[Record, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.action is None) == (self.transform is None):
            raise ValueError("A stage must declare exactly one action or transform")
        if self.execution not in {"nix", "relaxed", "host"}:
            raise ValueError(f"Unknown stage execution mode {self.execution!r}")
        if self.transform is not None:
            self._validate_transform_inputs()
        elif self.artifacts or self.execution != "nix":
            raise ValueError("Stage artifact inputs and execution modes require a transform")

    def _validate_transform_inputs(self) -> None:
        overlap = self.files.keys() & self.artifacts.keys()
        if overlap:
            raise ValueError(f"Stage has two sources for {', '.join(sorted(overlap))}")
        if any(record.ref.revision is None for record, _ in self.artifacts.values()):
            raise ValueError("Stage artifacts require an exact package revision")
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

    def validate_handlers(
        self,
        outputs: Mapping[str, tuple[str, ...]],
        *,
        inputs: Mapping[str, tuple[str, ...]],
    ) -> None:
        """Check declared stages against a flow's known handler outputs and inputs.

        Each stage must have a handler with matching outputs. If a handler
        consumes a file produced by another handler, declare that producer
        as a direct dependency in ``needs``.
        """
        producers = {name: stage for stage, names in outputs.items() for name in names}
        for stage in self._stages.values():
            self._validate_handler(stage, outputs, inputs.get(stage.name, ()), producers)

    def _validate_handler(
        self,
        stage: StageSpec,
        outputs: Mapping[str, tuple[str, ...]],
        inputs: tuple[str, ...],
        producers: Mapping[str, str],
    ) -> None:
        names = outputs.get(stage.name)
        if names is None:
            raise ValueError(f"Unknown stage handler {stage.name!r}")
        if set(stage.outputs) != set(names):
            raise ValueError(f"Stage {stage.name!r} must declare outputs {', '.join(names)}")
        for name in inputs:
            producer = producers.get(name)
            if producer is not None and producer not in stage.needs:
                raise ValueError(f"Stage {stage.name!r} needs {producer!r} for {name}")

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

    def _closure(self, targets: Iterable[str]) -> tuple[StageSpec, ...]:
        pending = list(targets)
        selected: set[str] = set()
        while pending:
            name = pending.pop()
            if name not in selected:
                stage = self[name]
                selected.add(name)
                pending.extend(stage.needs)
        return tuple(self[name] for name in self.order if name in selected)

    def _release(self, cap: str | None = None) -> tuple[StageSpec, ...]:
        if cap is not None:
            if self[cap].purpose != "release":
                raise ValueError(f"Expected a release stage, got {cap!r}")
            return self._closure((cap,))
        return tuple(self[name] for name in self.order if self[name].purpose == "release")

    def _ready(
        self, accepts: Callable[[StageSpec], bool], *, cap: str | None = None
    ) -> tuple[StageSpec, ...]:
        """Return release stages whose prerequisites and own inputs are available."""
        ready: dict[str, StageSpec] = {}
        for stage in self._release(cap):
            if all(name in ready for name in stage.needs) and accepts(stage):
                ready[stage.name] = stage
        return tuple(ready.values())

    @staticmethod
    def _targets(stages: tuple[StageSpec, ...]) -> tuple[str, ...]:
        """Find the terminal stages of a selected, dependency-closed subgraph."""
        upstream = {name for stage in stages for name in stage.needs}
        return tuple(stage.name for stage in stages if stage.name not in upstream)

    def _release_document(self) -> JSON:
        return [
            {
                "name": stage.name,
                "needs": tuple(sorted(stage.needs)),
                "outputs": tuple(sorted(stage.outputs)),
                **({"checks": tuple(sorted(stage.checks))} if stage.checks else {}),
            }
            for stage in sorted(self._release(), key=lambda stage: stage.name)
        ]


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
