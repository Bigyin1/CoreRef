"""Local project state shared by tool flows using the CoreRef manifest."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from .artifacts import SliceInputs, clean_commit, committed_slice
from .graph import Graph
from .outputs import Outputs, Producer
from .pipeline import BuildStage, Pipeline
from .records import JSON, InstalledRecord, Record
from .variants import ConfiguredPackage, PackageInput, Variants

if TYPE_CHECKING:
    from .catalog import Catalog
    from .manifest import Manifest
    from .route import Route, StageSpec


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

    def output_files(self, outputs: Mapping[str, Path]) -> dict[str, Path]:
        """Combine declared files, build outputs and the lockfile for a project slice."""
        return {**self.record.files, **outputs, "lockfile": self.require_lockfile()}

    def run_route(
        self,
        route: Route,
        targets: tuple[str, ...],
        bind: Callable[[tuple[StageSpec, ...]], tuple[BuildStage, ...]],
    ) -> dict[str, Path]:
        """Bind and execute only the selected route closure, then locate its outputs."""
        stages = route.closure(targets)
        Pipeline(self.build, bind(stages)).run(*targets)
        return {name: self.build / name for stage in stages for name in stage.outputs}

    def pack_build(
        self,
        build: Callable[[], Mapping[str, Path]],
        *,
        capture: Callable[[str | None, Mapping[str, Path]], SliceInputs],
        metadata: Callable[[SliceInputs], Mapping[str, JSON]],
    ) -> InstalledRecord:
        """Build named outputs and pack them under the current project slice."""
        files = self.output_files(build())
        inputs = capture(clean_commit(self.source), files)
        return self.outputs.pack(inputs, files, metadata(inputs))

    def publish_build(
        self,
        build: Callable[[], Mapping[str, Path]],
        *,
        capture: Callable[[str], SliceInputs],
        metadata: Callable[[SliceInputs], Mapping[str, JSON]],
        producers: Callable[[], Mapping[str, Producer]] | None = None,
    ) -> InstalledRecord:
        """Build from a stable checkout and publish output packages before their index."""
        with committed_slice(self.source, capture) as inputs:
            files = self.output_files(build())
            recipes = producers() if producers is not None else {}
        return self.outputs.publish(self.graph, inputs, files, metadata(inputs), producers=recipes)
