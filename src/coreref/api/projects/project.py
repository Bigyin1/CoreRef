"""Local project state shared by tool flows using the CoreRef manifest."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cached_property, partial
from pathlib import Path
from typing import TYPE_CHECKING

from ..._internal.pipeline import BuildStage, Pipeline
from ..._internal.producers import ProducerRecipes, RecipeStage
from ..._internal.snapshot import Snapshot, clean_commit, committed_slice
from ..build.outputs import Outputs, Producer, SliceInputs
from ..packages.records import JSON, InstalledRecord, Record
from ..packages.variants import ConfiguredPackage, PackageInput, Variants
from .graph import Graph

if TYPE_CHECKING:
    from ..build.route import Route, Stage, StageSpec
    from ..build.runtime import ProducerRuntime
    from ..packages.catalog import Catalog
    from .manifest import Manifest


class Project:
    """Bind a manifest to a checkout, its lockfile and a catalog for one command."""

    def __init__(
        self,
        directory: Path,
        manifest: Manifest,
        catalog: Catalog,
        *,
        groups: Mapping[str, tuple[str, ...]] | None = None,
        build_missing: bool = False,
    ) -> None:
        self.source = directory.resolve()
        self.manifest = manifest
        self.catalog = catalog
        self.record = manifest.local_record(self.source)
        self.lockfile = self.source / "manifest.lock"
        self.build_dir = self.source / ".build"
        self.outputs = Outputs(catalog, groups=groups, build_missing=build_missing)
        self.variants = Variants(catalog)

    def require_lockfile(self) -> Path:
        """Get the project lockfile used for reproducible builds."""
        if not self.lockfile.is_file():
            raise FileNotFoundError(f"Run the lock command and commit {self.lockfile}")
        return self.lockfile

    @cached_property
    def graph(self) -> Graph:
        return Graph(
            self.catalog,
            self.record,
            dependencies=self.manifest.requirements,
            lockfile=self.require_lockfile(),
        )

    @cached_property
    def dependencies(self) -> tuple[Record, ...]:
        return self.graph.dependencies(transitive=True)

    def lock(self, *additional_inputs: PackageInput) -> Path:
        """Refresh the project lock with all ordinary and configured requirements."""
        configured = (*self.manifest.configured_dependencies.values(), *additional_inputs)
        return self.variants.create_lockfile(
            self.record,
            requirements=self.manifest.requirements,
            inputs=configured,
            path=self.lockfile,
        )

    def install_variants(self, *, build_missing: bool = False) -> dict[str, ConfiguredPackage]:
        """Fetch the named configurations in the manifest using their locked recipes."""
        return self.variants.get_many(
            self.manifest.configured_dependencies,
            lockfile=self.lockfile,
            build_missing=build_missing,
        )

    def _slice_variants(self) -> Mapping[str, ConfiguredPackage]:
        return self.install_variants()

    @property
    def route(self) -> Route:
        raise NotImplementedError("This project does not define a build route")

    def _stage(self, spec: StageSpec) -> Stage:
        raise NotImplementedError(f"No handler for stage {spec.name!r}")

    def _prepare(self, stages: tuple[StageSpec, ...]) -> None:
        """Prepare inputs that can be fetched together for the selected stages."""

    def build(self, target: str | tuple[str, ...]) -> dict[str, Path]:
        """Execute the requested targets and their prerequisites."""
        targets = (target,) if isinstance(target, str) else target
        if not targets:
            raise ValueError("Select at least one build stage")
        stages = self.route._closure(targets)
        self._prepare(stages)
        tasks = tuple(self._task(spec) for spec in stages)
        Pipeline(self.build_dir, tasks).run(*targets)
        return {name: self.build_dir / name for stage in stages for name in stage.outputs}

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

    def _export_recipes(
        self,
        stages: tuple[StageSpec, ...],
        *,
        runner: Path | ProducerRuntime,
        configuration: Mapping[str, JSON],
        members: Mapping[str, tuple[str, ...]],
        external: Mapping[str, tuple[Producer, str]],
    ) -> dict[str, Producer]:
        """Export the selected stage descriptions as grouped Conan recipes."""
        recipes = ProducerRecipes(
            self.catalog, self.build_dir, runner=runner, configuration=configuration
        )
        names = {name for group in members.values() for name in group}
        return recipes.publish(
            self.record.ref,
            tuple(self._recipe(spec) for spec in stages if spec.name in names),
            members=members,
            outputs=self.outputs._groups,
            external=external,
        )

    def _recipe(self, spec: StageSpec) -> RecipeStage:
        stage = self._stage(spec)
        return RecipeStage(
            spec,
            tuple(name for name in stage.inputs if name not in stage.bundled_inputs),
            configuration={**stage.configuration, "bundled": stage.bundled_inputs},
        )

    def _publish_ready_recipes(self, _stages: tuple[StageSpec, ...], /) -> Mapping[str, Producer]:
        return {}

    def _output_files(self, outputs: Mapping[str, Path]) -> dict[str, Path]:
        """Combine declared files, build outputs and the lockfile for a project slice."""
        return {**self.record.files, **outputs, "lockfile": self.require_lockfile()}

    def _slice_metadata(self, _inputs: SliceInputs, /) -> Mapping[str, JSON]:
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
        return self.outputs.pack(
            inputs, files, {**snapshot.metadata(inputs), **self._slice_metadata(inputs)}
        )

    def publish(
        self,
        target: str | tuple[str, ...] | None = None,
        *,
        with_producers: bool = False,
    ) -> InstalledRecord:
        """Build and publish release targets, or select the ready ones automatically."""
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
        return self.outputs.publish(
            self.graph,
            inputs,
            files,
            {**snapshot.metadata(inputs), **self._slice_metadata(inputs)},
            producers=recipes,
        )
