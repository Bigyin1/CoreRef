"""Local project state shared by tool flows using the CoreRef manifest."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from ..._internal.producers import ProducerRecipes
from ..._internal.snapshot import Snapshot, clean_commit, committed_slice
from ..build.outputs import Outputs, Producer, SliceInputs
from ..packages.records import JSON, InstalledRecord, Record
from ..packages.variants import ConfiguredPackage, PackageInput, Variants
from .graph import Graph

if TYPE_CHECKING:
    from ..build.route import RecipeStage, Route, StageInputs, StageSpec
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
        self.build = self.source / ".build"
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

    def publish_producers(
        self,
        stages: tuple[RecipeStage, ...],
        *,
        runner: Path | ProducerRuntime,
        configuration: Mapping[str, JSON],
        members: Mapping[str, tuple[str, ...]],
        external: Mapping[str, tuple[Producer, str]],
    ) -> dict[str, Producer]:
        """Publish rebuild recipes for the project's named output groups."""
        recipes = ProducerRecipes(
            self.catalog, self.build, runner=runner, configuration=configuration
        )
        return recipes.publish(
            self.record.ref,
            stages,
            members=members,
            outputs=self.outputs._groups,
            external=external,
        )

    def _output_files(self, outputs: Mapping[str, Path]) -> dict[str, Path]:
        """Combine declared files, build outputs and the lockfile for a project slice."""
        return {**self.record.files, **outputs, "lockfile": self.require_lockfile()}

    def pack_build(
        self,
        route: Route,
        build: Callable[[], Mapping[str, Path]],
        *,
        stage_inputs: Callable[[StageSpec], StageInputs],
        metadata: Callable[[SliceInputs], Mapping[str, JSON]],
    ) -> InstalledRecord:
        """Build named outputs and pack them under the current project slice."""
        snapshot = Snapshot(self, route, stage_inputs=stage_inputs, variants=self._slice_variants)
        files = self._output_files(build())
        inputs = snapshot.inputs(clean_commit(self.source), files)
        return self.outputs.pack(inputs, files, {**snapshot.metadata(inputs), **metadata(inputs)})

    def publish_build(
        self,
        route: Route,
        build: Callable[[], Mapping[str, Path]],
        *,
        stage_inputs: Callable[[StageSpec], StageInputs],
        metadata: Callable[[SliceInputs], Mapping[str, JSON]],
        producers: Callable[[], Mapping[str, Producer]] | None = None,
    ) -> InstalledRecord:
        """Build from a stable checkout and publish output packages before their index."""
        snapshot = Snapshot(self, route, stage_inputs=stage_inputs, variants=self._slice_variants)
        with committed_slice(self.source, snapshot.inputs) as inputs:
            files = self._output_files(build())
            recipes = producers() if producers is not None else {}
        return self.outputs.publish(
            self.graph,
            inputs,
            files,
            {**snapshot.metadata(inputs), **metadata(inputs)},
            producers=recipes,
        )
