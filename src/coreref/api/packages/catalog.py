"""Public catalog API over Conan package operations."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from ..._internal.references import from_conan_reference
from ..errors import InvalidPackage
from .query import SearchQuery
from .records import (
    ConfiguredPackage,
    Dependency,
    InstalledRecord,
    LocalRecord,
    PackageInput,
    Record,
    Ref,
)

if TYPE_CHECKING:
    from ..._internal.package import LocalPackage, StoredPackage
    from ..projects.graph import Graph


class Catalog:
    """Pack, publish, and search packages using an already configured Conan client."""

    def __init__(
        self, home: str | Path | None = None, *, remote: str | None = None, timeout: float = 60
    ) -> None:
        from ..._internal.client import ConanClient
        from ..._internal.variants import Variants

        self._client = ConanClient(Path(home) if home is not None else None, remote, timeout)
        self._variants = Variants(self)

    def search_refs(self, query: SearchQuery, *, all_revisions: bool = False) -> tuple[Ref, ...]:
        """Find exact coordinates without downloading recipes or reading payloads."""
        pattern = query._conan_pattern(all_revisions=all_revisions)
        refs = []
        for reference, revisions in self._client.index(pattern).items():
            for revision in revisions.revisions:
                try:
                    ref = from_conan_reference(f"{reference}#{revision}")
                except InvalidPackage:
                    # A search can include Conan names outside the CoreRef grammar.
                    continue
                if query.matches_ref(ref):
                    refs.append(ref)
        return tuple(sorted(refs))

    def info(self, ref: Ref) -> Record:
        """Read recipe metadata without installing package contents."""
        return self._package(ref).info()

    def get(self, ref: Ref, *, with_sources: bool = False) -> InstalledRecord:
        """Get package files and optionally Git sources, resolving an unpinned revision."""
        return self._package(ref).get(with_sources=with_sources)

    def get_variant(
        self, package: PackageInput, *, lockfile: Path | None = None, build_missing: bool = False
    ) -> ConfiguredPackage:
        """Install one configured package from the lockfile and optionally build it."""
        return self._variants._get(package, lockfile=lockfile, build_missing=build_missing)

    def publish_variant_recipe(
        self,
        ref: Ref,
        *,
        options: Mapping[str, tuple[str | int | bool, ...]],
        inputs: Mapping[str, Path],
        runner: Path,
    ) -> Ref:
        """Publish a generator-backed recipe with these option domains and named inputs."""
        return self._variants.publish_generated_recipe(
            ref, options=options, inputs=inputs, runner=runner
        )

    def publish_variant(self, package: ConfiguredPackage) -> None:
        """Upload the exact installed variant to this catalog."""
        self._variants.publish(package)

    def publish(
        self,
        record: LocalRecord | Graph,
        *,
        dependencies: tuple[Dependency, ...] = (),
        lockfile: Path | None = None,
    ) -> InstalledRecord:
        """Publish a local description or reuse a previously resolved local graph."""
        self._client.require_remote()
        installed = self._pack(record, dependencies=dependencies, lockfile=lockfile)
        self._client.upload(str(installed.ref))
        return installed

    def _pack(
        self,
        record: LocalRecord | Graph,
        *,
        dependencies: tuple[Dependency, ...] = (),
        lockfile: Path | None = None,
    ) -> InstalledRecord:
        """Export a package to the local cache and return its exact reference."""
        from ..projects.graph import Graph

        if isinstance(record, Graph):
            if dependencies or lockfile is not None:
                raise InvalidPackage(
                    "A resolved graph already defines its dependencies and lockfile"
                )
            if record.catalog is not self:
                raise InvalidPackage("Pack the graph through its own catalog")
            return record._pack()
        return self._local_package(record, dependencies, lockfile).pack()

    def _upload(self, ref: Ref) -> None:
        """Upload an exact recipe and its locally packed binaries."""
        self._client.require_remote()
        if ref.revision is None:
            raise InvalidPackage("Upload requires an exact recipe revision")
        self._client.cache_path(str(ref))
        self._client.upload(str(ref))

    def _publish_graph(self, graph: Graph, record: LocalRecord) -> InstalledRecord:
        self._client.require_remote()
        if graph.catalog is not self:
            raise InvalidPackage("Publish the graph through its own catalog")
        installed = graph._pack(record)
        self._client.upload(str(installed.ref))
        return installed

    def _package(self, ref: Ref) -> StoredPackage:
        from ..._internal.package import StoredPackage

        return StoredPackage(self, ref)

    def _local_package(
        self,
        record: LocalRecord,
        dependencies: tuple[Dependency, ...],
        lockfile: Path | None,
    ) -> LocalPackage:
        from ..._internal.package import LocalPackage

        return LocalPackage(self, record, dependencies=dependencies, lockfile=lockfile)


__all__ = ["Catalog"]
