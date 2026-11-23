"""Operate on one checkout, from locking dependencies to releasing results.

A flow implements the route and stage actions. ``Project`` handles
dependency selection, local scheduling and publication of named outputs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import cached_property, partial
from pathlib import Path
from typing import TYPE_CHECKING

from ..._internal.artifacts import Producer
from ..._internal.cache import StageCache, StageInvocation
from ..._internal.pipeline import BuildStage, Pipeline
from ..._internal.producers import ProducerRecipes, RecipeStage
from ..._internal.runtime import ProducerRuntime
from ..._internal.snapshot import Snapshot, clean_commit, committed_slice
from ..build.outputs import Outputs
from ..build.route import CacheMode
from ..packages.records import JSON, ConfiguredPackage, InstalledRecord, PackageInput, Record
from .graph import Graph

if TYPE_CHECKING:
    from ..build.route import Route, Stage, StageSpec
    from ..packages.catalog import Catalog
    from .manifest import Manifest


class Project:
    """Bind a manifest, checkout and catalog for a build or CLI command.

    Use ``graph`` to inspect locked dependencies, ``lock()`` to refresh
    ``manifest.lock``, and ``build()`` or ``publish()`` to run stages.
    ``record`` describes the package being prepared, ``build_dir`` holds
    local stage outputs, and ``outputs`` resolves artifacts from published
    revisions. A flow supplies ``route`` and the ``_stage`` handler.
    """

    _output_groups: Mapping[str, tuple[str, ...]] = {}

    _producer_handler: str | None = None

    def __init__(
        self,
        directory: Path,
        manifest: Manifest,
        catalog: Catalog,
        *,
        build_missing: bool = False,
        cache_mode: CacheMode = CacheMode.OFF,
    ) -> None:
        """Bind the project directory to the given manifest and catalog.

        ``build_missing`` permits rebuilding missing published output groups.
        ``cache_mode`` controls shared-cache access for stages that opt in.
        The first access to ``graph`` resolves the dependencies.
        """
        self.source = directory.resolve()
        self.manifest = manifest
        self.catalog = catalog
        self.record = manifest.local_record(self.source)
        self.lockfile = self.source / "manifest.lock"
        self.build_dir = self.source / ".build"
        self.outputs = Outputs(catalog, groups=self._output_groups, build_missing=build_missing)
        self._stage_cache = StageCache(catalog, cache_mode)

    @property
    def cache_mode(self) -> CacheMode:
        """Return the configured policy for shared stage results."""
        return self._stage_cache.mode

    def _require_lockfile(self) -> Path:
        """Get the project lockfile used for reproducible builds."""
        if not self.lockfile.is_file():
            raise FileNotFoundError(f"Run the lock command and commit {self.lockfile}")
        return self.lockfile

    @cached_property
    def graph(self) -> Graph:
        """Resolve and cache the ordinary dependencies using ``manifest.lock``.

        The lockfile must exist. Resolve a new project instance to use a
        lockfile changed after the graph was first accessed.
        """
        return Graph(
            self.catalog,
            self.record,
            dependencies=self.manifest.requirements,
            lockfile=self._require_lockfile(),
        )

    def lock(self, *additional_inputs: PackageInput) -> Path:
        """Resolve declared dependencies afresh and save ``manifest.lock``.

        Include both ordinary dependencies and named configurations from the
        manifest. Pass ``additional_inputs`` to lock additional configured
        requirements discovered by the flow. Return the saved lockfile path.
        """
        configured = (*self.manifest.configured_dependencies.values(), *additional_inputs)
        return self.catalog._variants.create_lockfile(
            self.record,
            requirements=self.manifest.requirements,
            inputs=configured,
            path=self.lockfile,
        )

    def install_variants(self, *, build_missing: bool = False) -> dict[str, ConfiguredPackage]:
        """Install the configurations declared in the manifest.

        Return a mapping keyed by their declared names. Each configuration is
        resolved independently against the same project lockfile. Set
        ``build_missing=True`` to allow a missing binary to be built.
        """
        return self.catalog._variants.get_many(
            self.manifest.configured_dependencies,
            lockfile=self.lockfile,
            build_missing=build_missing,
        )

    def _slice_variants(self) -> Mapping[str, ConfiguredPackage]:
        return self.install_variants()

    @property
    def route(self) -> Route:
        """Return the flow's declarative stage graph.

        Subclasses implement this property to enable build and publication.
        """
        raise NotImplementedError("This project does not define a build route")

    def _stage(self, spec: StageSpec) -> Stage:
        """Return the runtime action and inputs for a declared stage."""
        raise NotImplementedError(f"No handler for stage {spec.name!r}")

    def _prepare(self, stages: tuple[StageSpec, ...]) -> None:
        """Prepare inputs that can be fetched together for selected stages.

        The stage specifications include prerequisites and are ordered
        dependencies first.
        """

    def build(self, target: str | tuple[str, ...]) -> dict[str, Path]:
        """Run one or more route targets and their prerequisites.

        The local executor can reuse outputs when their tracked inputs are
        unchanged. Return the declared outputs in the selected subgraph,
        including prerequisites, as paths under ``build_dir``.
        """
        targets = (target,) if isinstance(target, str) else target
        if not targets:
            raise ValueError("Select at least one build stage")
        stages = self.route._closure(targets)
        self._prepare(stages)
        tasks = tuple(self._task(spec) for spec in stages)
        Pipeline(self.build_dir, tasks).run(*targets)
        return {name: self.build_dir / name for stage in stages for name in stage.outputs}

    def _run_cached_stage(
        self,
        invocation: tuple[str, tuple[str, ...], str, str],
        directory: Path,
        *,
        inputs: tuple[str, ...],
        outputs: tuple[str, ...],
        action: Callable[[], None],
    ) -> str | None:
        """Run an isolated tool stage with the project's shared-cache policy.

        ``invocation`` supplies the stage name, command, digest-pinned image and
        platform in that order. Input and output names are relative to the
        working directory. Return a cache key on a hit, or ``None`` after
        executing the action.
        """
        if self.cache_mode is CacheMode.OFF:
            action()
            return None
        return self._stage_cache.run(
            StageInvocation(*invocation), directory, inputs=inputs, outputs=outputs, action=action
        )

    def _task(self, spec: StageSpec) -> BuildStage:
        stage = self._stage(spec)
        return BuildStage(
            name=spec.name,
            needs=spec.needs,
            outputs=spec.outputs,
            action=stage.action,
            inputs=stage.inputs,
            configuration={**stage.configuration, **stage.local_configuration},
            always_run=stage.always_run,
        )

    def _stage_ready(self, spec: StageSpec, *, for_recipe: bool) -> bool:
        """Decide whether a release stage has the inputs it needs.

        The ``for_recipe`` flag asks whether the stage can be exported for
        rebuilding in another environment. The base implementation only
        checks local input files for a regular release.
        """
        return not for_recipe and all(path.is_file() for path in self._stage(spec).files.values())

    def _ready_release(self, *, for_recipe: bool = False) -> tuple[StageSpec, ...]:
        accepts = partial(self._stage_ready, for_recipe=for_recipe)
        return self.route._ready(accepts, cap=self.manifest.release_through)

    def _release_targets(self) -> tuple[str, ...]:
        targets = self.route._targets(self._ready_release())
        if not targets:
            raise ValueError("No release stage is ready")
        return targets

    def _release_requirements(self) -> Mapping[str, tuple[str, ...]]:
        """Map release stages to additional checks that must run before publication."""
        return {}

    def _release_checks(self, targets: tuple[str, ...]) -> tuple[str, ...]:
        requirements = self._release_requirements()
        while requirements:
            selected = {stage.name for stage in self.route._closure(targets)}
            missing = dict.fromkeys(
                check
                for name, checks in requirements.items()
                if name in selected
                for check in checks
                if check not in selected
            )
            if not missing:
                break
            targets = (*targets, *missing)
        return targets

    def _announce_release(self, targets: tuple[str, ...]) -> None:
        """Report automatically selected targets before the build starts."""

    def _producer_members(self) -> Mapping[str, tuple[str, ...]]:
        """Map each rebuildable output group to the stage names it runs.

        Set ``_producer_handler`` to the installed flow's ``module:function``
        entry point when publishing rebuildable groups.
        """
        return {}

    def _prepare_producers(self, _stages: tuple[StageSpec, ...], /) -> Mapping[str, JSON]:
        """Prepare files for the selected rebuildable stages before recipe export.

        Return configuration shared by their handlers. CoreRef captures the
        producer's locked Python environment and exports the prepared recipes.
        """
        return {}

    def _producer_dependencies(self) -> Mapping[str, tuple[Record, str]]:
        """Bind staged filenames to named outputs from published dependency records.

        The mapping values are pairs containing the dependency record and its
        output name. CoreRef uses the record's exact selection to bind the
        corresponding recipe when one is registered.
        """
        return {}

    def _publish_ready_recipes(
        self,
        stages: tuple[StageSpec, ...],
    ) -> dict[str, Producer]:
        if self._producer_handler is None:
            return {}
        members = self._producer_members()
        names = {name for group in members.values() for name in group}
        selected = tuple(spec for spec in stages if spec.name in names)
        if not selected:
            return {}
        recipes = ProducerRecipes(
            self.catalog,
            self.build_dir,
            runner=ProducerRuntime(self.source, self._producer_handler),
            configuration=self._prepare_producers(selected),
        )
        return recipes.publish(
            self.record.ref,
            tuple(self._recipe(spec) for spec in selected),
            members=members,
            outputs=self.outputs._groups,
            external=self.outputs._producer_inputs(self._producer_dependencies()),
        )

    def _recipe(self, spec: StageSpec) -> RecipeStage:
        stage = self._stage(spec)
        return RecipeStage(
            spec,
            tuple(name for name in stage.inputs if name not in stage.bundled_inputs),
            configuration={**stage.configuration, "bundled": stage.bundled_inputs},
        )

    def _output_files(self, outputs: Mapping[str, Path]) -> dict[str, Path]:
        """Combine declared files, build outputs and the lockfile for a project slice."""
        return {**self.record.files, **outputs, "lockfile": self._require_lockfile()}

    def _slice_metadata(self, _variants: Mapping[str, str], /) -> Mapping[str, JSON]:
        """Provide additional flow metadata for the published project revision.

        The argument maps each named configuration to the exact binary
        reference selected for the slice.
        """
        return {}

    def _snapshot(self) -> Snapshot:
        return Snapshot(self, self.route, stage_inputs=self._stage, variants=self._slice_variants)

    def _pack(self, target: str | None) -> InstalledRecord:
        targets = (target,) if target is not None else self._release_targets()
        for name in targets:
            self.route._release(name)
        snapshot = self._snapshot()
        files = self._output_files(self.build(targets))
        inputs = snapshot.inputs(clean_commit(self.source), files)
        return self.outputs._pack(
            inputs, files, {**snapshot.metadata(inputs), **self._slice_metadata(inputs.variants)}
        )

    def publish(
        self,
        target: str | tuple[str, ...] | None = None,
        *,
        with_producers: bool = False,
    ) -> InstalledRecord:
        """Build release targets and publish their named outputs.

        Pass a release target or tuple of targets. Omit them to let the flow
        choose the ready terminal stages, subject to ``release_through``.
        Publication requires a clean Git checkout and an existing lockfile.
        CoreRef checks that the snapshot inputs stay unchanged during the
        build. With ``with_producers=True``, also publish recipe groups ready
        for rebuilding. The returned record is the published output index
        for the project revision.
        """
        if isinstance(target, str):
            target = (target,)
        targets = self._release_checks(target if target is not None else self._release_targets())
        for name in targets:
            self.route._release(name)
        if target is None:
            self._announce_release(targets)
        snapshot = self._snapshot()
        with committed_slice(self.source, snapshot.inputs) as inputs:
            files = self._output_files(self.build(targets))
            recipes = (
                self._publish_ready_recipes(self._ready_release(for_recipe=True))
                if with_producers
                else {}
            )
        return self.outputs._publish(
            self.graph,
            inputs,
            files,
            {**snapshot.metadata(inputs), **self._slice_metadata(inputs.variants)},
            producers=recipes,
        )
