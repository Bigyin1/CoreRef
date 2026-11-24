"""Execute bound stages in dependency order and materialize their selected outputs."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..api.errors import InvalidPackage
from .nix_store import Invocation, transform_reference

if TYPE_CHECKING:
    from ..api.build.route import Stage, StageSpec
    from ..api.packages.records import Record
    from .nix_store import NixStore


def _input_names(spec: StageSpec, stage: Stage, predecessors: Collection[str]) -> tuple[str, ...]:
    available = stage.files.keys() | stage.artifacts.keys() | set(predecessors)
    if stage.inputs is None:
        return tuple(sorted(available))
    if missing := set(stage.inputs) - available:
        names = ", ".join(sorted(missing))
        raise InvalidPackage(f"Stage {spec.name} has undeclared inputs: {names}")
    return stage.inputs


def _stage_files(
    bindings: Mapping[StageSpec, Stage],
    graph: Mapping[str, Mapping[str, Mapping[str, str]]],
    get_outputs: Callable[[Record, tuple[str, ...]], dict[str, Path]],
) -> dict[StageSpec, dict[str, Path | None]]:
    files = {
        spec: {name: stage.files.get(name) for name in _input_names(spec, stage, graph[spec.name])}
        for spec, stage in bindings.items()
    }
    requests: dict[int, tuple[Record, list[tuple[StageSpec, str, str]]]] = {}
    for spec, stage in bindings.items():
        for alias, (record, name) in stage.artifacts.items():
            if alias in files[spec]:
                # A logical ref can identify more than one immutable release index.
                requests.setdefault(id(record), (record, []))[1].append((spec, alias, name))
    for record, uses in requests.values():
        paths = get_outputs(record, tuple(sorted({name for _, _, name in uses})))
        for spec, alias, name in uses:
            files[spec][alias] = paths[name]
    return files


@dataclass(frozen=True)
class StageExecutor:
    nix: NixStore
    directory: Path
    build_missing: bool
    get_outputs: Callable[[Record, tuple[str, ...]], dict[str, Path]]

    def build(
        self,
        bindings: Mapping[StageSpec, Stage],
        graph: Mapping[str, Mapping[str, Mapping[str, str]]],
        *,
        selected: tuple[str, ...] | None,
    ) -> dict[str, Path]:
        files = _stage_files(bindings, graph, self.get_outputs)
        self.directory.mkdir(parents=True, exist_ok=True)
        outputs = self._run(bindings, graph, files, selected=selected)
        return outputs if selected is None else {name: outputs[name] for name in selected}

    def configured(
        self,
        spec: StageSpec,
        stage: Stage,
    ) -> Path:
        if stage.execution == "host" or len(spec.outputs) != 1:
            raise InvalidPackage("A configured dependency requires one native Nix output")
        results = self.build({spec: stage}, {spec.name: {}}, selected=None)
        return results[spec.outputs[0]].parent

    def _run(
        self,
        bindings: Mapping[StageSpec, Stage],
        graph: Mapping[str, Mapping[str, Mapping[str, str]]],
        files: Mapping[StageSpec, Mapping[str, Path | None]],
        *,
        selected: tuple[str, ...] | None,
    ) -> dict[str, Path]:
        batch: dict[str, Invocation] = {}
        outputs: dict[str, Path] = {}
        for spec, stage in bindings.items():
            if stage.execution == "host":
                outputs.update(
                    self.nix.realize(batch, self.directory, build_missing=self.build_missing)
                )
                batch.clear()
            inputs = {
                name: path or outputs.get(name) or graph[spec.name][name]
                for name, path in files[spec].items()
            }
            invocation = Invocation(
                name=spec.name,
                transform=transform_reference(stage.transform),
                execution=stage.execution,
                configuration=stage.configuration,
                inputs=inputs,
                outputs=spec.outputs,
                needs=spec.needs,
            )
            if stage.execution == "host":
                outputs.update(
                    self.nix.host(
                        invocation,
                        self.directory,
                        log=self.directory / f"{spec.name}.log",
                        build_missing=self.build_missing,
                        always_run=stage.always_run,
                    )
                )
            else:
                batch[spec.name] = invocation
        outputs.update(
            self.nix.realize(
                batch, self.directory, build_missing=self.build_missing, selected=selected
            )
        )
        return outputs
