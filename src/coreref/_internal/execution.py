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
    if stage.inputs is not None:
        return stage.inputs
    if stage.transform is None:
        return ()
    predecessors = (
        {output for name in spec.needs for output in route[name].outputs} if route else set()
    )
    return tuple(sorted(predecessors | stage.files.keys() | stage.artifacts.keys()))


def _prepare(bindings: Mapping[StageSpec, Stage]) -> None:
    selected: dict[Callable[[tuple[StageSpec, ...]], None], list[StageSpec]] = {}
    for spec, stage in bindings.items():
        if stage.prepare is not None:
            selected.setdefault(stage.prepare, []).append(spec)
    for prepare, stages in selected.items():
        prepare(tuple(stages))


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
    package_name: str
    build_missing: bool
    get_outputs: Callable[[Record, tuple[str, ...]], dict[str, Path]]

    def build(
        self,
        bindings: Mapping[StageSpec, Stage],
        route: Route,
        *,
        selected: tuple[str, ...] | None,
    ) -> dict[str, Path]:
        _prepare(bindings)
        files = _stage_files(bindings, route, self.get_outputs)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._run(bindings, files, selected=selected)
        names = (
            selected
            if selected is not None
            else tuple(name for spec in bindings for name in spec.outputs)
        )
        return {name: self.directory / name for name in names}

    def configured(
        self,
        spec: StageSpec,
        stage: Stage,
        *,
        route: Route | None = None,
    ) -> Path:
        if stage.execution == "host" or stage.transform is None or len(spec.outputs) != 1:
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
    ) -> None:
        batch: dict[str, Invocation] = {}
        pending: dict[str, StageOutput] = {}
        for spec, stage in bindings.items():
            if stage.transform is not None and stage.execution != "host":
                inputs = self._inputs(files[spec], pending)
                batch[spec.name] = self._invocation(spec, stage, inputs, batch=batch)
                pending.update(
                    {
                        name: StageOutput(spec.name, output_name(index), name)
                        for index, name in enumerate(spec.outputs)
                    }
                )
                continue
            # A host step needs its predecessors as local files.
            selected = None
            self._flush(batch)
            batch.clear()
            pending.clear()
            self._run_host(spec, stage, files[spec])
        self._flush(batch, selected=selected)

    def _flush(
        self, batch: Mapping[str, Invocation], *, selected: tuple[str, ...] | None = None
    ) -> None:
        self._link(
            self.nix.realize(
                batch, self.directory, build_missing=self.build_missing, selected=selected
            )
        )

    def _run_host(self, spec: StageSpec, stage: Stage, files: Mapping[str, Path | None]) -> None:
        if stage.action is not None:
            stage.action()
            if missing := [name for name in spec.outputs if not (self.directory / name).is_file()]:
                raise RuntimeError(f"Stage {spec.name} did not produce {', '.join(missing)}")
            return
        self._link(
            self.nix.host(
                self._invocation(spec, stage, self._inputs(files, {})),
                self.directory,
                log=self.directory / f"{spec.name}.log",
                build_missing=self.build_missing,
                always_run=stage.always_run,
            )
        )

    def _link(self, paths: Mapping[str, Path]) -> None:
        for name, result in paths.items():
            path = self.directory / name
            if path.is_symlink() and path.readlink() == result:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.unlink(missing_ok=True)
            path.symlink_to(result)

    def _inputs(
        self, files: Mapping[str, Path | None], pending: Mapping[str, StageOutput]
    ) -> dict[str, Path | StageOutput]:
        return {
            name: path or pending.get(name) or self.directory / name for name, path in files.items()
        }

    def _invocation(
        self,
        spec: StageSpec,
        stage: Stage,
        inputs: Mapping[str, Path | StageOutput],
        *,
        batch: Collection[str] = (),
    ) -> Invocation:
        if stage.transform is None:
            raise ValueError(f"Stage {spec.name} needs a Python transform")
        used = {path.stage for path in inputs.values() if isinstance(path, StageOutput)}
        return Invocation(
            name=f"{self.package_name}-{spec.name}",
            transform=transform_reference(stage.transform),
            execution=stage.execution,
            configuration=stage.configuration,
            inputs=inputs,
            outputs=spec.outputs,
            needs=tuple(name for name in spec.needs if name in batch and name not in used),
        )
