"""Public catalog API over Conan package operations."""

from __future__ import annotations

from pathlib import Path

from .._internal.client import ConanClient
from .._internal.package import LocalPackage, StoredPackage
from .._internal.references import from_conan_reference
from .errors import InvalidPackage
from .graph import Graph
from .query import SearchQuery
from .records import InstalledRecord, LocalRecord, Record, Ref


class Catalog:
    """Publish and search packages using an already configured Conan client."""

    def __init__(
        self, home: str | Path | None = None, *, remote: str | None = None, timeout: float = 60
    ) -> None:
        self._client = ConanClient(Path(home) if home is not None else None, remote, timeout)

    @property
    def cache_directory(self) -> Path:
        """The package storage directory selected by the configured client."""
        return self._client.cache_directory

    def search(self, query: SearchQuery, *, all_revisions: bool = False) -> tuple[Record, ...]:
        """Find latest matching revisions, or history when all_revisions is requested."""
        return tuple(
            self._package(ref).info()
            for ref in self.search_refs(query, all_revisions=all_revisions)
        )

    def search_refs(self, query: SearchQuery, *, all_revisions: bool = False) -> tuple[Ref, ...]:
        """Find exact coordinates without downloading recipes or reading payloads."""
        pattern = query._conan_pattern(all_revisions=all_revisions)
        if pattern is None:
            return ()
        refs = []
        for reference, revisions in self._client.index(pattern).items():
            for revision in revisions.revisions:
                ref = from_conan_reference(f"{reference}#{revision}", catalog=self)
                if query.matches_ref(ref):
                    refs.append(ref)
        return tuple(sorted(refs))

    def publish(
        self,
        record: LocalRecord | Graph,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> InstalledRecord:
        """Publish a local description or reuse a previously resolved local graph."""
        self._client.require_remote()
        if isinstance(record, Graph):
            if dependencies or lockfile is not None:
                raise InvalidPackage(
                    "A resolved graph already defines its dependencies and lockfile"
                )
            if record.catalog is not self:
                raise InvalidPackage("Publish the graph through its own catalog")
            return record._publish()
        return self._local_package(record, dependencies, lockfile).publish()

    def _package(self, ref: Ref) -> StoredPackage:
        return StoredPackage(Ref.from_fields(**ref.to_dict(), catalog=self))

    def _local_package(
        self,
        record: LocalRecord,
        dependencies: tuple[Ref, ...],
        lockfile: Path | None,
    ) -> LocalPackage:
        return LocalPackage(self, record, dependencies=dependencies, lockfile=lockfile)


__all__ = ["Catalog"]
