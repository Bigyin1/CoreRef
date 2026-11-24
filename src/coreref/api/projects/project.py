"""Bind the flow's Python stages to a checkout and a Nix store."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Mapping
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from ..._internal.execution import StageExecutor, stage_inputs
from ..build.route import Route
from ..errors import InvalidPackage, NotFound
from ..packages.records import Record, Ref
from .graph import Graph, lock_sources

if TYPE_CHECKING:
    from ..build.route import Stage, StageSpec
    from ..packages.catalog import Catalog
    from .manifest import Manifest

_LOGGER = logging.getLogger(__name__)


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
        self.ref = Ref(manifest.ref)
        self.lockfile = self.source / "manifest.lock"
        self.build_dir = self.source / ".build"
        self.build_missing = build_missing
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
            self.manifest,
            self.source,
            lockfile=self._require_lockfile(),
        )

    def lock(self, *additional: str | Ref) -> Path:
        """Pin dependencies and any additional source requests to exact store paths."""
        path = lock_sources(
            self.catalog,
            self.manifest,
            self.lockfile,
            tuple(map(str, additional)),
        )
        self.__dict__.pop("graph", None)
        self._projects.clear()
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

    def build(self, target: str | tuple[str, ...]) -> dict[str, Path]:
        """Build targets and their prerequisites and return their local files."""
        targets = (target,) if isinstance(target, str) else target
        return self._build(targets)

    def _build(
        self,
        targets: tuple[str, ...],
        *,
        selected: tuple[str, ...] | None = None,
        build_missing: bool = True,
        publishing: bool = False,
    ) -> dict[str, Path]:
        if not targets:
            raise ValueError("Select at least one build stage")
        stages = self.route._closure(targets, publishing=publishing)
        bindings = {spec: self._stage(spec) for spec in stages}
        return self._executor(build_missing).build(bindings, self.route, selected=selected)

    def _executor(self, build_missing: bool) -> StageExecutor:
        return StageExecutor(
            self.catalog._nix,
            self.build_dir,
            build_missing=build_missing,
            get_outputs=self.get_outputs,
        )

    def _dependency(self, record: Record) -> Project:
        from .manifest import Manifest

        if self.graph.info(record.ref).source != record.source:
            raise InvalidPackage(f"The source of {record.ref} differs from manifest.lock")
        if record.ref not in self._projects:
            source = self.graph.get_sources((record.ref,))[record.ref]
            project = type(self)(source, Manifest.load(source / "manifest.yml"), self.catalog)
            project.lockfile = self.lockfile
            project.build_dir = self.build_dir / "dependencies" / record.source.name
            project.build_missing = self.build_missing
            self._projects[record.ref] = project
        return self._projects[record.ref]

    def _output_stages(self, record: Record) -> dict[str, str]:
        route = self._dependency(record).route
        return {output: name for name in route.order for output in route[name].outputs}

    def _has_output(self, record: Record, name: str) -> bool:
        if not record.outputs or record.ref in self.catalog.editables:
            return name in self._output_stages(record)
        return name in record.outputs

    def get_outputs(self, record: Record, names: tuple[str, ...]) -> dict[str, Path]:
        """Retrieve selected dependency artifacts, building sources when needed."""
        if not names:
            return {}
        if record.outputs and record.ref not in self.catalog.editables:
            return self.catalog.get_outputs(record, names)
        stages = self._output_stages(record)
        if missing := set(names) - stages.keys():
            raise NotFound(f"{record.ref} has no outputs {', '.join(sorted(missing))}")
        targets = tuple(dict.fromkeys(stages[name] for name in names))
        paths = self._dependency(record)._build(
            targets, selected=names, build_missing=self.build_missing
        )
        return {name: paths[name].resolve(strict=True) for name in names}

    def _variant_stage(
        self,
        options: Mapping[str, str | int | bool],
    ) -> tuple[StageSpec, Stage]:
        """Bind a configured dependency to a Python stage. Flows define option semantics."""
        raise InvalidPackage(f"{self.ref} does not support options {dict(options)!r}")

    def install_variants(self, *, build_missing: bool = True) -> dict[str, Path]:
        """Realize each named configuration and return its store directory."""
        packages = {}
        for name, request in self.manifest.configured_dependencies.items():
            record = self.graph._resolve(request.ref)
            packages[name] = self._dependency(record).build_configured(
                request.options,
                build_missing=build_missing,
            )
        return packages

    def build_configured(
        self,
        options: Mapping[str, str | int | bool],
        *,
        build_missing: bool = True,
    ) -> Path:
        """Return the store directory for a flow-defined variant."""
        spec, stage = self._variant_stage(options)
        route = self.route if spec.needs and stage.inputs is None else None
        return self._executor(build_missing).configured(spec, stage, route=route)

    def _stage_ready(self, spec: StageSpec) -> bool:
        stage = self._stage(spec)
        inputs = stage_inputs(spec, stage, self.route)
        return (
            all(path.is_file() for path in stage.files.values())
            and (stage.ready is None or stage.ready())
            and all(
                self._has_output(record, name)
                for alias, (record, name) in stage.artifacts.items()
                if alias in inputs
            )
        )

    def release_targets(self) -> tuple[str, ...]:
        """Return the terminal stages with available inputs for a release."""
        ready = self.route._ready(self._stage_ready, cap=self.manifest.release_through)
        targets = self.route._targets(ready)
        if not targets:
            raise ValueError("No release stage is ready")
        return targets

    def publish(self, target: str | tuple[str, ...] | None = None) -> Path:
        """Upload selected results and return the exact store path of the release index.

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
        locked = lockfile.read_bytes()
        targets = (target,) if isinstance(target, str) else target or self.release_targets()
        if target is None:
            _LOGGER.info("Publishing through %s", ", ".join(targets))
        files = {
            name: (self.source / Path(path).expanduser()).resolve()
            for name, path in self.manifest.files.items()
        }
        files.update(self._build(targets, publishing=True))
        files["lockfile"] = lockfile
        if _clean_commit(self.source) != commit or lockfile.read_bytes() != locked:
            raise ValueError("Project inputs changed while building the release")
        return self.catalog._publish(self.source, self.manifest, files, commit=commit)


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
