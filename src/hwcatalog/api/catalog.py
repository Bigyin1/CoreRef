"""Public catalog API over Conan package operations."""

from pathlib import Path

from .._internal.conan import publication, records
from .._internal.conan.client import ConanClient
from .query import SearchQuery
from .records import LocalRecord, Record, Ref


class Catalog:
    """Publish and search packages using an already configured Conan client."""

    def __init__(
        self,
        home: str | Path | None = None,
        *,
        remote: str | None = None,
        timeout: float = 60,
    ) -> None:
        self._client = ConanClient(Path(home) if home is not None else None, remote, timeout)

    def search(self, query: SearchQuery = SearchQuery()) -> tuple[Record, ...]:
        """Find records matching the exact coordinate filters."""
        return records.search(self._client, query, catalog=self)

    def publish(
        self,
        record: LocalRecord,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> Record:
        """Publish one package with dependencies, optional Git checkout and stored files."""
        return publication.publish(
            self._client,
            record,
            dependencies,
            lockfile=lockfile,
            catalog=self,
        )


__all__ = ["Catalog"]
