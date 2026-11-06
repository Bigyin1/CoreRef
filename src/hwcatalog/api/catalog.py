"""Public catalog API over Conan package operations."""

from pathlib import Path

from .._internal.conan.client import ConanClient
from .._internal.conan.package import LocalPackage, StoredPackage
from .._internal.conan.references import from_conan_reference, search_pattern
from .query import SearchQuery
from .records import LocalRecord, Record, Ref


class Catalog:
    """Publish and search packages using an already configured Conan client."""

    def __init__(
        self, home: str | Path | None = None, *, remote: str | None = None, timeout: float = 60
    ) -> None:
        self._client = ConanClient(Path(home) if home is not None else None, remote, timeout)

    def search(self, query: SearchQuery = SearchQuery()) -> tuple[Record, ...]:
        """Find records matching the exact coordinate filters."""
        records = (package.recipe().record for package in self._packages(query))
        return tuple(sorted((r for r in records if query.matches(r)), key=lambda r: r.ref))

    def publish(
        self,
        record: LocalRecord,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> Record:
        """Publish one package from a detached snapshot of the local description."""
        return self._local_package(record, dependencies, lockfile).publish()

    def _package(self, ref: Ref) -> StoredPackage:
        return StoredPackage(Ref(**ref.to_dict(), catalog=self))

    def _local_package(
        self, record: LocalRecord, dependencies: tuple[Ref, ...], lockfile: Path | None
    ) -> LocalPackage:
        return LocalPackage(self, record, dependencies=dependencies, lockfile=lockfile)

    def _packages(self, query: SearchQuery) -> tuple[StoredPackage, ...]:
        pattern = search_pattern(query)
        if pattern is None:
            return ()
        return tuple(
            StoredPackage.from_index(from_conan_reference(ref, catalog=self), revisions)
            for ref, revisions in self._client.index(pattern).items()
        )


__all__ = ["Catalog"]
