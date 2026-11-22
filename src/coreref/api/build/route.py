"""Load local YAML routes and select stages without invoking their handlers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

from ..packages.records import JSON


class StageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: str = Field(pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_-]*$")
    outputs: tuple[str, ...] = Field(strict=False)
    needs: tuple[str, ...] = Field(default=(), strict=False)
    purpose: Literal["release", "development"] = "release"


@dataclass(frozen=True, kw_only=True)
class Stage:
    """Bind a stage to its action, input files and parameters."""

    action: Callable[[], object]
    inputs: tuple[str, ...] = ()
    files: Mapping[str, Path] = field(default_factory=dict)
    configuration: Mapping[str, JSON] = field(default_factory=dict)
    local_configuration: Mapping[str, JSON] = field(default_factory=dict)
    bundled_inputs: tuple[str, ...] = ()
    always_run: bool = False


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    imports: list[str] = Field(default_factory=list)
    stages: list[StageSpec] = Field(default_factory=list)


class Route:
    """A validated graph of uniquely named build stages."""

    def __init__(self, stages: list[StageSpec]) -> None:
        self._stages = {stage.name: stage for stage in stages}
        if len(stages) != len(self._stages):
            raise ValueError("Duplicate stage in flow")
        _validate_outputs(stages)
        for stage in stages:
            self._validate_needs(stage)
        try:
            self.order = tuple(
                TopologicalSorter({stage.name: stage.needs for stage in stages}).static_order()
            )
        except CycleError as exc:
            raise ValueError(f"Cycle in flow stages: {exc.args[1]}") from exc

    @classmethod
    def load(cls, project: Path, path: Path = Path("flow.yml")) -> Route:
        """Read an entry file and its local imports, relative to the project root."""
        project = project.resolve()
        return cls(_read_stages(project, project / path))

    def validate_handlers(
        self,
        outputs: Mapping[str, tuple[str, ...]],
        *,
        inputs: Mapping[str, tuple[str, ...]],
    ) -> None:
        """Match declarative stages to the files their handlers produce and consume."""
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

    def __getitem__(self, name: str) -> StageSpec:
        try:
            return self._stages[name]
        except KeyError as exc:
            raise ValueError(f"Unknown stage {name!r}") from exc

    def __contains__(self, name: str) -> bool:
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
            }
            for stage in sorted(self._release(), key=lambda stage: stage.name)
        ]


def _read_stages(project: Path, path: Path) -> list[StageSpec]:
    seen: set[Path] = set()
    visiting: set[Path] = set()
    stages: list[StageSpec] = []

    def visit(file: Path) -> None:
        file = file.resolve()
        if not file.is_relative_to(project):
            raise ValueError(f"Flow YAML must be within the project: {file}")
        if file in visiting:
            raise ValueError(f"Cycle in flow imports at {file}")
        if file in seen:
            return
        visiting.add(file)
        document = _read_document(file)
        for dependency in document.imports:
            visit(file.parent / dependency)
        stages.extend(document.stages)
        visiting.remove(file)
        seen.add(file)

    visit(path)
    return stages


def _read_document(file: Path) -> _Document:
    try:
        document = _Document.model_validate(yaml.safe_load(file.read_text(encoding="utf-8")))
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid flow YAML {file}: {exc}") from exc
    for dependency in document.imports:
        if Path(dependency).is_absolute():
            raise ValueError(f"Expected a relative flow import in {file}: {dependency}")
    return document


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


__all__ = ["Route", "Stage", "StageSpec"]
