"""Public catalog API over Conan package operations."""

from pathlib import Path

from .._internal.client import ConanClient
from .._internal.package import LocalPackage, StoredPackage
from .._internal.references import from_conan_reference
from .query import SearchQuery
from .records import LocalRecord, Record, Ref


class Catalog:
    """Publish and search packages using an already configured Conan client."""

    def __init__(
        self, home: str | Path | None = None, *, remote: str | None = None, timeout: float = 60
    ) -> None:
        self._client = ConanClient(Path(home) if home is not None else None, remote, timeout)

    def search(
        self, query: SearchQuery = SearchQuery(), *, all_revisions: bool = False
    ) -> tuple[Record, ...]:
        """Find latest matching revisions, or history when all_revisions is requested."""
        records = (
            package.recipe().record
            for package in self._packages(query, all_revisions=all_revisions)
        )
        return query.select(records)

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

    def _packages(
        self, query: SearchQuery, *, all_revisions: bool = False
    ) -> tuple[StoredPackage, ...]:
        pattern = query.conan_pattern(all_revisions=all_revisions)
        if pattern is None:
            return ()
        packages = []
        for reference, revisions in self._client.index(pattern).items():
            for revision in revisions.revisions:
                ref = from_conan_reference(f"{reference}#{revision}", catalog=self)
                if query.matches_ref(ref):
                    packages.append(StoredPackage(ref).load())
        return tuple(packages)


__all__ = ["Catalog"]
