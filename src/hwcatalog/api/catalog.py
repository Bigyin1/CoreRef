"""Public catalog API over Conan package operations."""

from pathlib import Path

from .._internal.conan import graph, publication, records
from .._internal.conan.client import ConanClient
from .models.graph import GraphResult
from .models.query import SearchQuery
from .models.records import Record, Ref


class Catalog:
    """Publish, query and fetch packages using an already configured Conan client."""

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

    def resolve_local(
        self, record: Record, *, dependencies: tuple[Ref, ...] = (), lockfile: Path | None = None
    ) -> GraphResult:
        """Resolve local project data independently of its publication status."""
        return graph.resolve_local(
            self._client, record, dependencies=dependencies, lockfile=lockfile, catalog=self
        )

    def publish(
        self, record: Record, *, dependencies: tuple[Ref, ...] = (), lockfile: Path | None = None
    ) -> Record:
        """Publish one new package with already published dependencies."""
        return publication.publish(
            self._client, record, dependencies, lockfile=lockfile, catalog=self
        )

    def publish_file(self, path: str | Path, *, lockfile: Path | None = None) -> Record:
        """Publish one package described by a JSON document."""
        return publication.publish_file(self._client, path, lockfile=lockfile, catalog=self)

    def create_lockfile(self, refs: tuple[Ref, ...], path: str | Path) -> Path:
        """Create or update a project-owned Conan lockfile."""
        return publication.create_lockfile(self._client, refs, path)


__all__ = ["Catalog"]
