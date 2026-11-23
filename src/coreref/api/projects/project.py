"""Operate on one checkout, from locking dependencies to releasing results.

A flow implements the route and stage actions. ``Project`` handles
dependency selection, local scheduling and publication of named outputs.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from functools import cached_property, partial
from json import dumps, loads
from pathlib import Path
from platform import python_version
from typing import TYPE_CHECKING

from ..._internal.artifacts import Producer
from ..._internal.cache import StageCache, TransformInvocation
from ..._internal.hashing import file_digest
from ..._internal.pipeline import BuildStage, Pipeline
from ..._internal.producer import check_outputs, transform_identity, transform_reference, workspace
from ..._internal.producers import ProducerRecipes, RecipeStage
from ..._internal.runtime import ProducerRuntime
from ..._internal.snapshot import Snapshot, clean_commit, committed_slice
from ..build.outputs import Outputs
from ..build.route import CacheMode, Route
from ..packages.records import JSON, ConfiguredPackage, InstalledRecord, PackageInput, Record, Ref
from .graph import Graph

if TYPE_CHECKING:
    from ..build.route import Stage, StageSpec
    from ..packages.catalog import Catalog
    from .manifest import Manifest


class Project:
    """Bind a manifest, checkout and catalog for a build or CLI command.

    Use ``graph`` to inspect locked dependencies, ``lock()`` to refresh
    ``manifest.lock``, and ``build()`` or ``publish()`` to run stages.
    ``record`` describes the package being prepared, ``build_dir`` holds
    local stage outputs, and ``outputs`` resolves artifacts from published
    revisions. A flow supplies ``route`` and the bindings in ``stages``.
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

    @cached_property
    def route(self) -> Route:
        """Return the flow's declarative stage graph.

        By default the graph is loaded from the project's flow.yml. A flow
        can override it to compose fragments or apply its own selection.
        """
        return Route.load(self.source)

    @cached_property
    def stages(self) -> Mapping[str, Stage | Callable[[], Stage]]:
        """Bind stage names to transforms or lazy stage factories."""
        return {}

    def _stage(self, spec: StageSpec) -> Stage:
        """Return the runtime action and inputs for a declared stage."""
        try:
            stage = self.stages[spec.name]
        except KeyError as exc:
            raise ValueError(f"No handler for stage {spec.name!r}") from exc
        return stage() if callable(stage) else stage

    def _prepare(self, stages: tuple[StageSpec, ...]) -> None:
        """Prepare inputs that can be fetched together for selected stages.

        The stage specifications include prerequisites and are ordered
        dependencies first.
        """

    @staticmethod
    def _prepare_stages(bindings: Mapping[StageSpec, Stage]) -> None:
        selected: dict[Callable[[tuple[StageSpec, ...]], None], list[StageSpec]] = {}
        for spec, stage in bindings.items():
            if stage.prepare is not None:
                selected.setdefault(stage.prepare, []).append(spec)
        for prepare, stages in selected.items():
            prepare(tuple(stages))

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
        bindings = {spec: self._stage(spec) for spec in stages}
        self._prepare_stages(bindings)
        bindings = self._fetch_stage_artifacts(bindings)
        tasks = tuple(self._task(spec, stage) for spec, stage in bindings.items())
        Pipeline(self.build_dir, tasks).run(*targets)
        return {name: self.build_dir / name for stage in stages for name in stage.outputs}

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

    def _task(self, spec: StageSpec, stage: Stage) -> BuildStage:
        configuration = {**stage.configuration, **stage.local_configuration}
        inputs = self._stage_inputs(spec, stage)
        action = stage.action
        if stage.transform is not None:
            environment = self._transform_environment(stage.transform)
            configuration = {
                "configuration": dict(stage.configuration),
                "local": dict(stage.local_configuration),
                "transform": environment,
                "source_files": {
                    name: file_digest(path) if path.is_file() else None
                    for name, path in stage.files.items()
                    if name in inputs
                },
                "cache_mode": self.cache_mode.value if stage.cache else None,
            }
            action = partial(self._execute_transform, spec, stage, stage.transform, environment)
        if action is None:
            raise ValueError(f"Missing implementation of stage {spec.name!r}")
        return BuildStage(
            name=spec.name,
            needs=spec.needs,
            outputs=spec.outputs,
            action=action,
            inputs=tuple(name for name in inputs if name not in stage.files)
            if stage.transform is not None
            else inputs,
            configuration=configuration,
            always_run=stage.always_run,
        )

    def _stage_inputs(self, spec: StageSpec, stage: Stage) -> tuple[str, ...]:
        if stage.inputs is not None:
            return stage.inputs
        if stage.transform is None:
            return ()
        from_predecessors = {output for name in spec.needs for output in self.route[name].outputs}
        return tuple(sorted(from_predecessors | stage.files.keys() | stage.artifacts.keys()))

    def _transform_environment(
        self, transform: Callable[[Path, Path, Mapping[str, JSON]], None]
    ) -> dict[str, JSON]:
        lock = self.source / "uv.lock"
        return {
            **transform_identity(transform),
            "python": python_version(),
            "uv.lock": file_digest(lock) if lock.is_file() else None,
        }

    def _execute_transform(
        self,
        spec: StageSpec,
        stage: Stage,
        transform: Callable[[Path, Path, Mapping[str, JSON]], None],
        environment: dict[str, JSON],
    ) -> None:
        inputs = {
            name: stage.files.get(name, self.build_dir / name)
            for name in self._stage_inputs(spec, stage)
        }
        with workspace(self.build_dir, spec.name, inputs, spec.outputs) as (work, log):

            def run() -> None:
                transform(work, log, loads(dumps(dict(stage.configuration), allow_nan=False)))
                check_outputs(work, spec.outputs, log)

            if stage.cache and self.cache_mode is not CacheMode.OFF:
                invocation = TransformInvocation(
                    spec.name, environment, dict(stage.configuration), spec.outputs
                )
                key = self._stage_cache.run(
                    invocation, work, inputs=tuple(inputs), outputs=spec.outputs, action=run
                )
                if key is not None:
                    log.write_text(f"Restored shared cache entry {key}\n", encoding="utf-8")
            else:
                run()

    def _stage_ready(self, spec: StageSpec, *, for_recipe: bool) -> bool:
        """Decide whether a release stage has the inputs it needs.

        The ``for_recipe`` flag asks whether the stage can be exported for
        rebuilding in another environment. The default checks source files
        for transforms and their prerequisites in either mode.
        """
        stage = self._stage(spec)
        return (
            all(path.is_file() for path in stage.files.values())
            and (stage.ready is None or stage.ready())
            and all(
                name in self.outputs.available(record, producer_only=for_recipe)
                for record, name in self._stage_artifacts(spec, stage).values()
            )
        )

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
        return {spec.name: spec.checks for spec in self.route._release() if spec.checks}

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
        """Group transforms into recipes. By default each gets its own recipe."""
        groups: dict[str, list[str]] = {}
        for spec in self.route._release():
            if spec.outputs and self._stage(spec).transform is not None:
                groups.setdefault(spec.recipe or spec.name, []).append(spec.name)
        return {name: tuple(stages) for name, stages in groups.items()}

    def _prepare_producers(self, stages: tuple[StageSpec, ...], /) -> Mapping[str, JSON]:
        """Build prerequisites that must be captured as prepared inputs."""
        selected = {stage.name for stage in stages}
        prepared = tuple(
            dict.fromkeys(name for stage in stages for name in stage.needs if name not in selected)
        )
        if prepared:
            self.build(prepared)
        self._prepare_stages({spec: self._stage(spec) for spec in stages})
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
            outputs=self._recipe_outputs(members),
            external=self.outputs._producer_inputs(self._producer_dependencies()),
        )

    def _recipe_outputs(self, members: Mapping[str, tuple[str, ...]]) -> dict[str, tuple[str, ...]]:
        result = dict(self._output_groups)
        for group, names in members.items():
            if group in result:
                continue
            specs = (self.route[name] for name in names)
            result[group] = tuple(
                output
                for spec in specs
                for output in (spec.outputs if spec.recipe_outputs is None else spec.recipe_outputs)
            )
        return result

    def _recipe(self, spec: StageSpec) -> RecipeStage:
        stage = self._stage(spec)
        if stage.transform is not None:
            return RecipeStage(
                spec,
                self._stage_inputs(spec, stage),
                configuration=dict(stage.configuration),
                transform=transform_reference(stage.transform),
                files=stage.files,
                artifacts=self.outputs._producer_inputs(self._stage_artifacts(spec, stage)),
            )
        return RecipeStage(
            spec,
            tuple(name for name in (stage.inputs or ()) if name not in stage.bundled_inputs),
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
