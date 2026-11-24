"""Execute bound stages in dependency order and materialize their selected outputs."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from ..api.errors import InvalidPackage
from .nix_store import Invocation, StageOutput, output_name, transform_reference

if TYPE_CHECKING:
    from ..api.build.route import Route, Stage, StageSpec
    from ..api.packages.records import Record
    from .nix_store import NixStore


def stage_inputs(spec: StageSpec, stage: Stage, route: Route | None = None) -> tuple[str, ...]:
    predecessors = (
        {output for name in spec.needs for output in route[name].outputs} if route else set()
    )
    available = predecessors | stage.files.keys() | stage.artifacts.keys()
    if stage.inputs is None:
        return tuple(sorted(available))
    if missing := set(stage.inputs) - available:
        names = ", ".join(sorted(missing))
        raise InvalidPackage(f"Stage {spec.name} has undeclared inputs: {names}")
    return stage.inputs


def _stage_files(
    bindings: Mapping[StageSpec, Stage],
    route: Route | None,
    get_outputs: Callable[[Record, tuple[str, ...]], dict[str, Path]],
) -> dict[StageSpec, dict[str, Path | None]]:
    files = {
        spec: {name: stage.files.get(name) for name in stage_inputs(spec, stage, route)}
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
        route: Route,
        *,
        selected: tuple[str, ...] | None,
    ) -> dict[str, Path]:
        files = _stage_files(bindings, route, self.get_outputs)
        self.directory.mkdir(parents=True, exist_ok=True)
        outputs = self._run(bindings, files, selected=selected)
        return outputs if selected is None else {name: outputs[name] for name in selected}

    def configured(
        self,
        spec: StageSpec,
        stage: Stage,
        *,
        route: Route | None = None,
    ) -> Path:
        if stage.execution == "host" or len(spec.outputs) != 1:
            raise InvalidPackage("A configured dependency requires one native Nix output")
        self.directory.mkdir(parents=True, exist_ok=True)
        files = _stage_files({spec: stage}, route, self.get_outputs)[spec]
        invocation = self._invocation(spec, stage, self._inputs(files, {}))
        results = self.nix.realize(
            {spec.name: invocation}, self.directory, build_missing=self.build_missing
        )
        return results[spec.outputs[0]].parent

    def _run(
        self,
        bindings: Mapping[StageSpec, Stage],
        files: Mapping[StageSpec, Mapping[str, Path | None]],
        *,
        selected: tuple[str, ...] | None,
    ) -> dict[str, Path]:
        batch: dict[str, Invocation] = {}
        outputs: dict[str, Path | StageOutput] = {}
        for spec, stage in bindings.items():
            if stage.execution != "host":
                inputs = self._inputs(files[spec], outputs)
                batch[spec.name] = self._invocation(spec, stage, inputs, batch=batch)
                outputs.update(
                    {
                        name: StageOutput(spec.name, output_name(index), name)
                        for index, name in enumerate(spec.outputs)
                    }
                )
                continue
            outputs.update(
                self.nix.realize(batch, self.directory, build_missing=self.build_missing)
            )
            batch.clear()
            invocation = self._invocation(spec, stage, self._inputs(files[spec], outputs))
            outputs.update(self._run_host(invocation, stage))
        outputs.update(
            self.nix.realize(
                batch, self.directory, build_missing=self.build_missing, selected=selected
            )
        )
        return {name: path for name, path in outputs.items() if isinstance(path, Path)}

    def _run_host(self, invocation: Invocation, stage: Stage) -> dict[str, Path]:
        return self.nix.host(
            invocation,
            self.directory,
            log=self.directory / f"{invocation.name}.log",
            build_missing=self.build_missing,
            always_run=stage.always_run,
        )

    @staticmethod
    def _inputs(
        files: Mapping[str, Path | None], outputs: Mapping[str, Path | StageOutput]
    ) -> dict[str, Path | StageOutput]:
        return {name: path if path is not None else outputs[name] for name, path in files.items()}

    @staticmethod
    def _invocation(
        spec: StageSpec,
        stage: Stage,
        inputs: Mapping[str, Path | StageOutput],
        *,
        batch: Collection[str] = (),
    ) -> Invocation:
        used = {path.stage for path in inputs.values() if isinstance(path, StageOutput)}
        return Invocation(
            name=spec.name,
            transform=transform_reference(stage.transform),
            execution=stage.execution,
            configuration=stage.configuration,
            inputs=inputs,
            outputs=spec.outputs,
            needs=tuple(name for name in spec.needs if name in batch and name not in used),
        )
