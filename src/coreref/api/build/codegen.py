"""Generate executable route bindings from a trusted flow package's YAML map."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from keyword import iskeyword
from pathlib import Path
from pprint import pformat
from typing import Literal

import yaml  # type: ignore[import-untyped]
from jinja2 import Environment, PackageLoader, StrictUndefined
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from .route import StageSpec

_ENVIRONMENT = Environment(
    loader=PackageLoader("coreref._internal"),
    autoescape=False,  # noqa: S701 - interpolation uses Python repr.
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
_ENVIRONMENT.filters["pyrepr"] = repr
_TEMPLATE = _ENVIRONMENT.get_template("flow.py.j2")


class _Node(StageSpec):
    transform: str | None = None
    bind: str | None = None
    files: dict[str, str] = Field(default_factory=dict)
    inputs: tuple[str, ...] | None = Field(default=None, strict=False)
    configuration: dict[str, JsonValue] = Field(default_factory=dict)
    execution: Literal["nix", "relaxed", "host"] | None = None

    @model_validator(mode="after")
    def _implementation(self) -> _Node:
        if self.transform is None and self.bind is None:
            raise ValueError(f"Stage {self.name!r} needs a transform or a binding")
        return self


class _FlowMap(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    modules: dict[str, list[_Node]]
    variants: dict[str, dict[str, dict[str, object]]] = Field(default_factory=dict)
    adapters: dict[str, str] = Field(default_factory=dict)
    project_type: str = "coreref.api.projects:Project"


def _symbol(value: str) -> tuple[str, str]:
    module, _, name = value.partition(":")
    if any(not part.isidentifier() or iskeyword(part) for part in (*module.split("."), name)):
        raise ValueError(f"Expected module:symbol, got {value!r}")
    return module, name


def _stage_specs(nodes: list[_Node]) -> dict[str, StageSpec]:
    specs: dict[str, StageSpec] = {}
    fields = set(StageSpec.model_fields)
    for node in nodes:
        if node.name in specs:
            raise ValueError(f"Duplicate stage {node.name!r} in flow map")
        specs[node.name] = StageSpec.model_validate(node.model_dump(include=fields))
    return specs


def _variants(
    patches: dict[str, dict[str, dict[str, object]]], specs: dict[str, StageSpec]
) -> dict[str, dict[str, dict[str, object]]]:
    variants: dict[str, dict[str, dict[str, object]]] = {}
    for name, overrides in patches.items():
        normalized = {}
        for stage, patch in overrides.items():
            if stage not in specs or "name" in patch:
                raise ValueError(f"Invalid stage override {stage!r} in variant {name!r}")
            effective = StageSpec.model_validate({**specs[stage].model_dump(), **patch})
            normalized[stage] = effective.model_dump(include=set(patch))
        variants[name] = normalized
    return variants


def _imports(entries: Iterable[str | None]) -> dict[str, tuple[str, str, str]]:
    symbols = dict.fromkeys(entry for entry in entries if entry is not None)
    return {entry: (*_symbol(entry), f"_handler_{index}") for index, entry in enumerate(symbols)}


def _parameters(nodes: list[_Node]) -> dict[str, dict[str, object]]:
    fields = {"files", "inputs", "configuration", "execution"}
    return {
        node.name: options
        for node in nodes
        if (options := node.model_dump(include=fields, exclude_defaults=True))
    }


def generate_route(path: Path) -> str:
    """Return a Python module generated from a flow author's YAML map.

    Modules declare named stages, edges and transform entry points. A named
    variant can configure existing stages for a supported route composition.
    Declare file roles and configuration for a direct transform. Optional
    adapters bind stage-specific inputs and are initialized once per project.
    Commit or package the generated module and use its ``make_route`` and
    ``bind_stages`` at runtime. Only generate code from trusted maps.
    """
    graph = _FlowMap.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    nodes = [node for group in graph.modules.values() for node in group]
    specs = _stage_specs(nodes)
    imports = _imports((*graph.adapters.values(), *(node.transform for node in nodes)))
    missing = {node.bind for node in nodes if node.bind is not None} - graph.adapters.keys()
    if missing:
        raise ValueError(f"Unknown adapters in stage map: {', '.join(sorted(missing))}")
    project_module, project_name = _symbol(graph.project_type)
    return (
        _TEMPLATE.render(
            project_module=project_module,
            project_name=project_name,
            imports=imports.values(),
            modules=pformat(
                {name: tuple(node.name for node in group) for name, group in graph.modules.items()}
            ),
            specs=pformat(
                {
                    name: spec.model_dump(exclude_defaults=True, exclude={"name"})
                    for name, spec in specs.items()
                }
            ),
            variants=pformat(_variants(graph.variants, specs)),
            handlers=[
                (node.name, imports[node.transform][2] if node.transform else "None", node.bind)
                for node in nodes
            ],
            parameters=pformat(_parameters(nodes)),
            adapters={name: imports[entry][2] for name, entry in graph.adapters.items()},
        ).rstrip()
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("map", type=Path)
    parser.add_argument("output", type=Path)
    command = parser.parse_args()
    command.output.write_text(generate_route(command.map), encoding="utf-8")


if __name__ == "__main__":
    main()
