"""Load and bind the declarative stage map shipped by a Flow package."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import replace
from functools import partial
from importlib import import_module
from keyword import iskeyword
from typing import TYPE_CHECKING, Literal, cast

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ..packages.records import JSON
from .route import Route, Stage, StageSpec, StageTransform

if TYPE_CHECKING:
    from pathlib import Path

    from ..projects.project import Project

_Binder = Callable[[StageSpec, StageTransform], Stage]


class _Node(StageSpec):
    transform: str
    bind: str | None = None
    files: dict[str, str] = Field(default_factory=dict)
    inputs: tuple[str, ...] | None = Field(default=None, strict=False)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    execution: Literal["nix", "relaxed", "host"] | None = None


class _Definition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    modules: dict[str, list[_Node]]
    variants: dict[str, dict[str, dict[str, object]]] = Field(default_factory=dict)
    adapters: dict[str, str] = Field(default_factory=dict)


def _symbol(value: str) -> tuple[str, str]:
    module, _, name = value.partition(":")
    if any(not part.isidentifier() or iskeyword(part) for part in (*module.split("."), name)):
        raise ValueError(f"Expected module:symbol, got {value!r}")
    return module, name


def _load(value: str) -> object:
    module, name = _symbol(value)
    return getattr(import_module(module), name)


def _stage_specs(nodes: Iterable[_Node]) -> dict[str, StageSpec]:
    specs: dict[str, StageSpec] = {}
    fields = set(StageSpec.model_fields)
    for node in nodes:
        if node.name in specs:
            raise ValueError(f"Duplicate stage {node.name!r} in stage map")
        specs[node.name] = StageSpec.model_validate(node.model_dump(include=fields))
    return specs


def _apply_variant(specs: dict[str, StageSpec], overrides: Mapping[str, dict[str, object]]) -> None:
    for name, patch in overrides.items():
        if name in specs:
            specs[name] = StageSpec.model_validate({**specs[name].model_dump(), **patch})


def _bind_stage(
    node: _Node, spec: StageSpec, adapter: _Binder | None, roles: Mapping[str, Path]
) -> Stage:
    transform = cast(StageTransform, _load(node.transform))
    stage = Stage(transform=transform) if adapter is None else adapter(spec, transform)
    if missing := set(node.files.values()) - roles.keys():
        raise ValueError(f"Stage {spec.name!r} needs file roles: {', '.join(sorted(missing))}")
    return replace(
        stage,
        files={**stage.files, **{name: roles[role] for name, role in node.files.items()}},
        configuration={**stage.configuration, **cast(dict[str, JSON], node.configuration)},
        inputs=stage.inputs if node.inputs is None else node.inputs,
        execution=node.execution or stage.execution,
    )


class StageMap:
    """Compose routes from a trusted Flow package's module and variant declarations."""

    def __init__(self, definition: _Definition) -> None:
        self._definition = definition
        nodes = [node for module in definition.modules.values() for node in module]
        self._specs = _stage_specs(nodes)
        self._nodes = {node.name: node for node in nodes}
        missing = {
            node.bind for node in nodes if node.bind is not None
        } - definition.adapters.keys()
        if missing:
            raise ValueError(f"Unknown adapters in stage map: {', '.join(sorted(missing))}")
        for value in (*definition.adapters.values(), *(node.transform for node in nodes)):
            _symbol(value)
        for variant, overrides in definition.variants.items():
            for name, patch in overrides.items():
                if name not in self._specs or "name" in patch:
                    raise ValueError(f"Invalid stage override {name!r} in variant {variant!r}")
            _apply_variant(self._specs.copy(), overrides)

    @classmethod
    def load(cls, path: Path) -> StageMap:
        """Read a stage map whose transform and adapter modules are trusted."""
        definition = _Definition.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        return cls(definition)

    @property
    def modules(self) -> tuple[str, ...]:
        """Return the names of modules available to projects using this Flow."""
        return tuple(self._definition.modules)

    def route(self, modules: Iterable[str], variants: Iterable[str] = ()) -> Route:
        """Select modules and apply overrides in the supplied variant order."""
        specs = {
            node.name: self._specs[node.name]
            for module in modules
            for node in self._definition.modules[module]
        }
        for name in variants:
            _apply_variant(specs, self._definition.variants[name])
        return Route(list(specs.values()))

    def bind(
        self, project: Project, route: Route, *, files: Mapping[str, Path] | None = None
    ) -> dict[str, Callable[[], Stage]]:
        """Bind selected stage handlers lazily and reuse each project's adapters."""
        adapters: dict[str, _Binder] = {}
        roles = {} if files is None else files
        stages: dict[str, Callable[[], Stage]] = {}
        for name in route.order:
            node = self._nodes[name]
            bind = node.bind
            if bind is not None and bind not in adapters:
                factory = cast(
                    "Callable[[Project], _Binder]", _load(self._definition.adapters[bind])
                )
                adapters[bind] = factory(project)
            adapter = adapters[bind] if bind is not None else None
            stages[name] = partial(_bind_stage, node, route[name], adapter, roles)
        return stages
