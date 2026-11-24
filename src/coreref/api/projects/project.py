"""Bind the flow's Python stages to a checkout and a Nix store."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import cached_property
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from ..._internal.hashing import file_digest
from ..._internal.nix_store import (
    Invocation,
    StageOutput,
    output_name,
    store_entry,
    transform_reference,
)
from ..build.outputs import Outputs
from ..build.route import Route
from ..errors import InvalidPackage, NotFound
from ..packages.catalog import _Source
from ..packages.records import ConfiguredPackage, InstalledRecord, PackageInput, Record, Ref
from .graph import Graph, lock_sources

if TYPE_CHECKING:
    from ..build.route import Stage, StageSpec
    from ..packages.catalog import Catalog
    from .manifest import Manifest


class Project:
    """Build the stages declared by a flow using pinned package sources.

    Use ``lock()`` to refresh and commit manifest.lock, ``graph`` to inspect
    its dependencies, and ``build()`` or ``publish()`` to select results.
    A flow supplies ``route`` and ``stages``.
    """

    def __init__(
        self,
        directory: Path,
        manifest: Manifest,
        catalog: Catalog,
        *,
        build_missing: bool = True,
    ) -> None:
        """Bind the checkout and its catalog without resolving dependencies yet."""
        self.source = directory.resolve()
        self.manifest = manifest
        self.catalog = catalog
        self.record = manifest.local_record(self.source)
        self.lockfile = self.source / "manifest.lock"
        self.build_dir = self.source / ".build"
        self.outputs = Outputs(catalog, build=self._dependency_outputs)
        self.build_missing = build_missing
        self._build_local = True
        self._projects: dict[Ref, Project] = {}

    def _require_lockfile(self) -> Path:
        if not self.lockfile.is_file():
            raise FileNotFoundError(f"Run the lock command and commit {self.lockfile}")
        return self.lockfile

    @cached_property
    def graph(self) -> Graph:
        """Read the dependency snapshot. Fetch selected sources through the graph."""
        return Graph(
            self.catalog,
            self.record,
            dependencies=self.manifest.requirements,
            lockfile=self._require_lockfile(),
        )

    def lock(self, *additional_inputs: PackageInput) -> Path:
        """Write the exact dependency source trees and release selections."""
        path = lock_sources(
            self.catalog,
            self.manifest,
            self.lockfile,
            tuple(value.requirement for value in additional_inputs),
        )
        self.__dict__.pop("graph", None)
        return path

    @cached_property
    def route(self) -> Route:
        """Return the flow's stage declarations."""
        raise NotImplementedError("The flow must provide a route")

    @cached_property
    def stages(self) -> Mapping[str, Stage | Callable[[], Stage]]:
        """Bind stage names to Python functions or lazy stage factories."""
        return {}

    def _stage(self, spec: StageSpec) -> Stage:
        try:
            stage = self.stages[spec.name]
        except KeyError as error:
            raise ValueError(f"No handler for stage {spec.name!r}") from error
        return stage() if callable(stage) else stage

    def _prepare(self, stages: tuple[StageSpec, ...]) -> None:
        """Prepare inputs shared by the selected stages, in dependency order."""

    @staticmethod
    def _prepare_stages(bindings: Mapping[StageSpec, Stage]) -> None:
        selected: dict[Callable[[tuple[StageSpec, ...]], None], list[StageSpec]] = {}
        for spec, stage in bindings.items():
            if stage.prepare is not None:
                selected.setdefault(stage.prepare, []).append(spec)
        for prepare, stages in selected.items():
            prepare(tuple(stages))

    def build(self, target: str | tuple[str, ...]) -> dict[str, Path]:
        """Build targets and their prerequisites and return their local files."""
        targets = (target,) if isinstance(target, str) else target
        return self._build(targets)

    def _build(
        self,
        targets: tuple[str, ...],
        *,
        selected: tuple[str, ...] | None = None,
    ) -> dict[str, Path]:
        if not targets:
            raise ValueError("Select at least one build stage")
        stages = self.route._closure(targets)
        self._prepare(stages)
        bindings = {spec: self._stage(spec) for spec in stages}
        self._prepare_stages(bindings)
        bindings = self._fetch_stage_artifacts(bindings)
        self._run(bindings, selected=selected)
        names = (
            selected
            if selected is not None
            else tuple(name for stage in stages for name in stage.outputs)
        )
        return {name: self.build_dir / name for name in names}

    def _fetch_stage_artifacts(self, bindings: dict[StageSpec, Stage]) -> dict[StageSpec, Stage]:
        selections = {spec: self._stage_artifacts(spec, stage) for spec, stage in bindings.items()}
        requests: dict[Ref, tuple[Record, set[str]]] = {}
        for selection in selections.values():
            for record, name in selection.values():
                requests.setdefault(record.ref, (record, set()))[1].add(name)
        if not requests:
            return bindings
        paths = {
            ref: self.outputs.get(record, tuple(sorted(names)))
            for ref, (record, names) in requests.items()
        }
        return {
            spec: replace(
                stage,
                artifacts={},
                inputs=self._stage_inputs(spec, stage),
                files={
                    **stage.files,
                    **{
                        alias: paths[record.ref][name]
                        for alias, (record, name) in selections[spec].items()
                    },
                },
            )
            for spec, stage in bindings.items()
        }

    def _stage_artifacts(self, spec: StageSpec, stage: Stage) -> dict[str, tuple[Record, str]]:
        inputs = self._stage_inputs(spec, stage)
        return {name: binding for name, binding in stage.artifacts.items() if name in inputs}

    def _stage_inputs(self, spec: StageSpec, stage: Stage) -> tuple[str, ...]:
        if stage.inputs is not None:
            return stage.inputs
        if stage.transform is None:
            return ()
        predecessors = {output for name in spec.needs for output in self.route[name].outputs}
        return tuple(sorted(predecessors | stage.files.keys() | stage.artifacts.keys()))

    def _run(
        self,
        bindings: Mapping[StageSpec, Stage],
        *,
        selected: tuple[str, ...] | None,
    ) -> None:
        self.build_dir.mkdir(parents=True, exist_ok=True)
        batch: dict[str, Invocation] = {}
        pending: dict[str, StageOutput] = {}
        for spec, stage in bindings.items():
            if stage.transform is not None and stage.execution != "host":
                batch[spec.name] = self._invocation(spec, stage, pending, batch)
                pending.update(
                    {
                        name: StageOutput(spec.name, output_name(index), name)
                        for index, name in enumerate(spec.outputs)
                    }
                )
                continue
            selected = None
            self._flush(batch)
            batch.clear()
            pending.clear()
            self._run_host(spec, stage)
        self._flush(batch, selected=selected)

    def _flush(
        self,
        batch: dict[str, Invocation],
        *,
        selected: tuple[str, ...] | None = None,
    ) -> None:
        paths = self.catalog._nix.realize(
            batch,
            self.build_dir,
            build_missing=self._build_local,
            selected=selected,
        )
        self._link(paths)

    def _run_host(self, spec: StageSpec, stage: Stage) -> None:
        if stage.action is not None:
            stage.action()
            if missing := [name for name in spec.outputs if not (self.build_dir / name).is_file()]:
                raise RuntimeError(f"Stage {spec.name} did not produce {', '.join(missing)}")
            return
        result = self.catalog._nix.host(
            self._invocation(spec, stage, {}, {}),
            self.build_dir,
            log=self.build_dir / f"{spec.name}.log",
            build_missing=self._build_local,
            always_run=stage.always_run,
        )
        self._link(result)

    def _link(self, paths: Mapping[str, Path]) -> None:
        for name, result in paths.items():
            path = self.build_dir / name
            if path.is_symlink() and path.readlink() == result:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.unlink(missing_ok=True)
            path.symlink_to(result)

    def _invocation(
        self,
        spec: StageSpec,
        stage: Stage,
        pending: Mapping[str, StageOutput],
        batch: Mapping[str, Invocation],
    ) -> Invocation:
        if stage.transform is None:
            raise ValueError(f"Stage {spec.name} needs a Python transform")
        inputs: dict[str, Path | StageOutput] = {}
        for name in self._stage_inputs(spec, stage):
            if name in stage.files:
                inputs[name] = stage.files[name]
            else:
                inputs[name] = pending.get(name) or self.build_dir / name
        used = {path.stage for path in inputs.values() if isinstance(path, StageOutput)}
        return Invocation(
            name=f"{self.record.ref.package_name}-{spec.name}",
            transform=transform_reference(stage.transform),
            execution=stage.execution,
            configuration={**stage.configuration, **stage.local_configuration},
            inputs=inputs,
            outputs=spec.outputs,
            needs=tuple(name for name in spec.needs if name in batch and name not in used),
        )

    def _dependency(self, record: Record) -> Project:
        from .manifest import Manifest

        if record.ref not in self._projects:
            source = self.graph.get_sources((record.ref,))[record.ref]
            project = type(self)(source, Manifest.load(source / "manifest.yml"), self.catalog)
            project.lockfile = self.lockfile
            project.build_dir = self.build_dir / "dependencies" / str(record.ref).replace("/", "-")
            project.build_missing = self.build_missing
            project._build_local = self.build_missing
            self._projects[record.ref] = project
        return self._projects[record.ref]

    def _output_stages(self, record: Record) -> dict[str, str]:
        route = self._dependency(record).route
        return {output: name for name in route.order for output in route[name].outputs}

    def _has_output(self, record: Record, name: str) -> bool:
        links = self.outputs.links(record)
        if links is None or self.catalog._is_editable(record.ref):
            return name in self._output_stages(record)
        return name in links

    def _dependency_outputs(self, record: Record, names: tuple[str, ...]) -> dict[str, Path]:
        stages = self._output_stages(record)
        if missing := set(names) - stages.keys():
            raise NotFound(f"{record.ref} has no outputs {', '.join(sorted(missing))}")
        targets = tuple(dict.fromkeys(stages[name] for name in names))
        paths = self._dependency(record)._build(targets, selected=names)
        return {name: paths[name].resolve(strict=True) for name in names}

    def _variant_stage(
        self,
        options: Mapping[str, str | int | bool],
    ) -> tuple[StageSpec, Stage]:
        """Bind a configured dependency to a Python stage. Flows define option semantics."""
        raise InvalidPackage(f"{self.record.ref} does not support options {dict(options)!r}")

    def install_variants(self, *, build_missing: bool = True) -> dict[str, ConfiguredPackage]:
        """Realize the requested options independently, even for the same component."""
        packages = {}
        for name, request in self.manifest.configured_dependencies.items():
            record = self.graph._resolve(request.requirement)
            package = self._dependency(record).build_configured(
                request.options,
                build_missing=build_missing,
            )
            packages[name] = ConfiguredPackage(record.ref, package.directory)
        return packages

    def build_configured(
        self,
        options: Mapping[str, str | int | bool],
        *,
        build_missing: bool = True,
    ) -> ConfiguredPackage:
        """Realize a flow-defined variant with its own Nix identity."""
        spec, stage = self._variant_stage(options)
        if stage.execution == "host" or len(spec.outputs) != 1:
            raise InvalidPackage("A configured dependency requires one native Nix output")
        self.build_dir.mkdir(parents=True, exist_ok=True)
        invocation = self._invocation(spec, stage, {}, {})
        results = self.catalog._nix.realize(
            {spec.name: invocation},
            self.build_dir,
            build_missing=build_missing,
        )
        return ConfiguredPackage(self.record.ref, results[spec.outputs[0]].parent)

    def _stage_ready(self, spec: StageSpec) -> bool:
        stage = self._stage(spec)
        return (
            all(path.is_file() for path in stage.files.values())
            and (stage.ready is None or stage.ready())
            and all(
                self._has_output(record, name)
                for record, name in self._stage_artifacts(spec, stage).values()
            )
        )

    def release_targets(self) -> tuple[str, ...]:
        """Return the terminal stages with available inputs for a release."""
        ready = self.route._ready(self._stage_ready, cap=self.manifest.release_through)
        targets = self.route._targets(ready)
        if not targets:
            raise ValueError("No release stage is ready")
        return targets

    def _release_checks(self, targets: tuple[str, ...]) -> tuple[str, ...]:
        while True:
            selected = {stage.name for stage in self.route._closure(targets)}
            missing = dict.fromkeys(
                check
                for name in self.route.order
                if name in selected
                for check in self.route[name].checks
                if check not in selected
            )
            if not missing:
                return targets
            targets = (*targets, *missing)

    def _announce_release(self, targets: tuple[str, ...]) -> None:
        """Report automatically selected targets before the build starts."""

    def publish(self, target: str | tuple[str, ...] | None = None) -> InstalledRecord:
        """Upload selected results and return the immutable release index.

        Publication uses a clean committed checkout and the existing lockfile.
        The index is copied after the artifacts and can be registered with
        another catalog to retrieve the individual files.
        """
        if self.catalog._nix.cache is None:
            raise ValueError("Configure a writable Nix binary cache before publishing")
        if self.catalog.editables:
            raise ValueError("Publication requires dependency sources pinned by the lockfile")
        commit = _clean_commit(self.source)
        lockfile = self._require_lockfile()
        locked = file_digest(lockfile)
        targets = (target,) if isinstance(target, str) else target or self.release_targets()
        targets = self._release_checks(targets)
        for name in targets:
            self.route._release(name)
        if target is None:
            self._announce_release(targets)
        files = {**self.record.files, **self.build(targets), "lockfile": lockfile}
        if _clean_commit(self.source) != commit or file_digest(lockfile) != locked:
            raise ValueError("Project inputs changed while building the release")
        return self._publish(commit, files)

    def _publish(self, commit: str, files: dict[str, Path]) -> InstalledRecord:
        nix = self.catalog._nix
        source = nix.source(
            self.source,
            f"coreref-source-{self.record.ref.package_name}",
            tracked=True,
        )
        outputs = {
            name: self._publish_path(path.resolve(strict=True)) for name, path in files.items()
        }
        with TemporaryDirectory() as temporary:
            index = Path(temporary) / "release.json"
            ref, metadata = self._release_metadata(commit, source, outputs)
            document = {"ref": str(ref), "source": metadata.model_dump(mode="json")}
            index.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
            index_path = nix.add(index, "coreref-release.json")
            nix.upload((source, *outputs.values()))
            nix.upload((index_path,))
        record = metadata.record(ref, file_names=("release.json",))
        return InstalledRecord(record, files={"release.json": index_path}, source=source)

    def _publish_path(self, path: Path) -> Path:
        try:
            store_entry(path)
        except InvalidPackage:
            return self.catalog._nix.add(path, "coreref-artifact")
        return path

    def _release_metadata(
        self,
        commit: str,
        source: Path,
        outputs: Mapping[str, Path],
    ) -> tuple[Ref, _Source]:
        index = _Source(
            source=str(source),
            dependencies=tuple(map(str, self.manifest.requirements)),
            outputs={name: str(path) for name, path in outputs.items()},
            payload={"type": self.manifest.type, "commit": commit, "data": self.manifest.payload},
        )
        document = index.model_dump(mode="json")
        revision = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
        ref = Ref(f"{self.record.ref}#{revision}")
        return ref, index


def _clean_commit(source: Path) -> str:
    arguments = ["git", "-C", str(source)]
    try:
        dirty = subprocess.check_output(  # noqa: S603 - fixed Git query with separate arguments.
            [*arguments, "status", "--porcelain", "--untracked-files=all", "--", "."],
            text=True,
        ).strip()
        if dirty:
            raise ValueError("Publishing requires a clean Git checkout")
        return subprocess.check_output(  # noqa: S603 - fixed Git query with separate arguments.
            [*arguments, "rev-parse", "HEAD"],
            text=True,
        ).strip()
    except subprocess.CalledProcessError as error:
        raise ValueError("Publishing requires a committed Git checkout") from error
