"""Public catalog API over Conan package operations."""

from __future__ import annotations

from pathlib import Path

from ..._internal.client import ConanClient
from ..._internal.package import LocalPackage, StoredPackage
from ..._internal.references import from_conan_reference
from ..errors import InvalidPackage
from ..projects.graph import Graph
from .query import SearchQuery
from .records import Dependency, InstalledRecord, LocalRecord, Record, Ref


class Catalog:
    """Pack, publish, and search packages using an already configured Conan client."""

    def __init__(
        self, home: str | Path | None = None, *, remote: str | None = None, timeout: float = 60
    ) -> None:
        self._client = ConanClient(Path(home) if home is not None else None, remote, timeout)

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
        return StoredPackage(self, ref)

    def _local_package(
        self,
        record: LocalRecord,
        dependencies: tuple[Dependency, ...],
        lockfile: Path | None,
    ) -> LocalPackage:
        return LocalPackage(self, record, dependencies=dependencies, lockfile=lockfile)


__all__ = ["Catalog"]
