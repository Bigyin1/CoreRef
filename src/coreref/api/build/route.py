"""Declare and validate stage graphs for CoreRef builds.

The route controls stage dependencies and output names. The flow
provides actions and the files and configuration needed to run them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from graphlib import CycleError, TopologicalSorter
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field

from ..packages.records import JSON, Record

StageTransform = Callable[[Path, Path, Mapping[str, JSON]], None]


class CacheMode(str, Enum):
    """Control shared-cache access for stages that opt into it.

    ``OFF`` bypasses the shared cache. ``READ`` can restore a matching
    result. ``WRITE`` can also publish a successful result on a miss.
    """

    OFF = "off"
    READ = "read"
    WRITE = "write"


class StageSpec(BaseModel):
    """Describe a named stage before binding it to a handler.

    ``needs`` names direct predecessor stages. ``outputs`` lists relative
    paths within the build directory. ``purpose`` determines whether the
    stage can participate in publication. During publication ``checks``
    adds validation stages. ``recipe`` groups stages in a rebuildable
    package, and ``recipe_outputs`` selects which of their files it exports.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: str = Field(pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_-]*$")
    outputs: tuple[str, ...] = Field(strict=False)
    needs: tuple[str, ...] = Field(default=(), strict=False)
    purpose: Literal["release", "development"] = "release"
    checks: tuple[str, ...] = Field(default=(), strict=False)
    recipe: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_-]*$")
    recipe_outputs: tuple[str, ...] | None = Field(default=None, strict=False)


@dataclass(frozen=True, kw_only=True)
class Stage:
    """Bind a declared stage to a callable and its tracked inputs.

    A transform receives an isolated working directory, a log path and
    JSON configuration. CoreRef stages its declared inputs and checks
    its outputs before making them available. Use a module-level function
    to publish the same transform as a rebuild recipe. Set cache=True
    when the inputs and configuration fully determine its outputs.

    If a transform omits ``inputs``, CoreRef uses its ``files`` and all
    outputs of direct predecessors. Explicit ``inputs`` select filenames
    from those sources or from the build directory. The keys in ``files``
    are staged names and its values are source paths.

    ``action`` is called with no arguments and uses the build directory
    directly. ``configuration`` contributes to both local
    execution and release inputs, while ``local_configuration`` applies
    only to local runs. ``bundled_inputs`` identifies inputs already
    shipped with the producer's implementation. Set ``always_run`` when
    the action must execute even if the local executor can reuse its outputs.
    ``ready()`` checks stage-specific prerequisites. A shared
    ``prepare(stages)`` callback can compute inputs for selected stages in one
    operation. ``artifacts`` binds staged input names to named outputs from
    published packages. CoreRef fetches them locally or pins their producers
    in the rebuild recipe.
    """

    action: Callable[[], object] | None = None
    transform: StageTransform | None = None
    cache: bool = False
    inputs: tuple[str, ...] | None = None
    files: Mapping[str, Path] = field(default_factory=dict)
    configuration: Mapping[str, JSON] = field(default_factory=dict)
    local_configuration: Mapping[str, JSON] = field(default_factory=dict)
    bundled_inputs: tuple[str, ...] = ()
    always_run: bool = False
    ready: Callable[[], bool] | None = None
    prepare: Callable[[tuple[StageSpec, ...]], None] | None = None
    artifacts: Mapping[str, tuple[Record, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (self.action is None) == (self.transform is None):
            raise ValueError("A stage must declare exactly one action or transform")
        if self.transform is None:
            if self.cache or self.artifacts:
                raise ValueError("Stage cache and artifact inputs require a transform")
        else:
            self._validate_transform_inputs()

    def _validate_transform_inputs(self) -> None:
        overlap = self.files.keys() & self.artifacts.keys()
        if overlap:
            raise ValueError(f"Stage has two sources for {', '.join(sorted(overlap))}")
        if any(record.ref.revision is None for record, _ in self.artifacts.values()):
            raise ValueError("Stage artifacts require an exact package revision")
        for name in (*self.files, *self.artifacts, *(self.inputs or ())):
            _output_path(name)


class _Override(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    add_needs: tuple[str, ...] = Field(default=(), strict=False)


class _Document(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    imports: list[str] = Field(default_factory=list)
    use: list[str] = Field(default_factory=list)
    stages: list[StageSpec] = Field(default_factory=list)
    overrides: dict[str, _Override] = Field(default_factory=dict)

    def expand(self, fragments: Mapping[str, tuple[StageSpec, ...]]) -> list[StageSpec]:
        if len(self.use) != len(set(self.use)):
            raise ValueError("Duplicate route module in use")
        missing = set(self.use) - fragments.keys()
        if missing:
            raise ValueError(f"Unknown route module in use: {', '.join(sorted(missing))}")
        return [stage for name in self.use for stage in fragments[name]] + self.stages


class _ConfiguredDocument(_Document):
    model_config = ConfigDict(extra="allow", strict=True)


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

    @classmethod
    def load(
        cls,
        project: Path,
        path: Path = Path("flow.yml"),
        *,
        fragments: Mapping[str, tuple[StageSpec, ...]] | None = None,
        select: Callable[[list[StageSpec], Mapping[str, object]], list[StageSpec]] | None = None,
    ) -> Route:
        """Compose a route and validate the selected stage graph.

        Imports are relative to the YAML file that declares them and must
        resolve inside the project. Names in ``use`` select supplied route
        fragments. Entries in ``overrides`` can extend a stage's ``needs``
        with ``add_needs``.

        A flow can supply ``select`` to interpret its own keys from the root
        YAML document and choose stages before graph validation. The callback
        receives the composed stages and the flow-specific keys. It validates
        those keys and returns the stages to use. Loading invokes no stage
        handlers.
        """
        project = project.resolve()
        documents = _read_documents(project, project / path, allow_options=select is not None)
        stages = [stage for document in documents for stage in document.expand(fragments or {})]
        stages = _apply_overrides(stages, documents)
        if select is not None:
            stages = select(stages, documents[-1].model_extra or {})
        return cls(stages)

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
        if set(stage.recipe_outputs or ()) - set(stage.outputs):
            raise ValueError(f"Stage {stage.name!r} exports unknown recipe outputs")
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
                **({"recipe": stage.recipe} if stage.recipe is not None else {}),
                **(
                    {"recipe_outputs": tuple(sorted(stage.recipe_outputs))}
                    if stage.recipe_outputs is not None
                    else {}
                ),
            }
            for stage in sorted(self._release(), key=lambda stage: stage.name)
        ]


def _read_documents(project: Path, path: Path, *, allow_options: bool) -> list[_Document]:
    root = path.resolve()
    seen: set[Path] = set()
    visiting: set[Path] = set()
    documents: list[_Document] = []

    def visit(file: Path) -> None:
        file = file.resolve()
        if not file.is_relative_to(project):
            raise ValueError(f"Flow YAML must be within the project: {file}")
        if file in visiting:
            raise ValueError(f"Cycle in flow imports at {file}")
        if file in seen:
            return
        visiting.add(file)
        document = _read_document(file, allow_options=allow_options and file == root)
        for dependency in document.imports:
            visit(file.parent / dependency)
        documents.append(document)
        visiting.remove(file)
        seen.add(file)

    visit(root)
    return documents


def _read_document(file: Path, *, allow_options: bool) -> _Document:
    schema = _ConfiguredDocument if allow_options else _Document
    try:
        document = schema.model_validate(yaml.safe_load(file.read_text(encoding="utf-8")))
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"Invalid flow YAML {file}: {exc}") from exc
    for dependency in document.imports:
        if Path(dependency).is_absolute():
            raise ValueError(f"Expected a relative flow import in {file}: {dependency}")
    return document


def _apply_overrides(stages: list[StageSpec], documents: list[_Document]) -> list[StageSpec]:
    needs: dict[str, tuple[str, ...]] = {}
    for document in documents:
        for name, override in document.overrides.items():
            needs[name] = (*needs.get(name, ()), *override.add_needs)
    unknown = needs.keys() - {stage.name for stage in stages}
    if unknown:
        raise ValueError(f"Unknown stage in overrides: {', '.join(sorted(unknown))}")
    return [
        stage.model_copy(update={"needs": (*stage.needs, *needs[stage.name])})
        if stage.name in needs
        else stage
        for stage in stages
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
