"""Public catalog API over Conan package operations."""

from pathlib import Path
from types import TracebackType

from .._internal.conan import graph, materialization, publication, records
from .._internal.conan.client import ConanClient
from .errors import CatalogError
from .models.graph import GraphResult
from .models.query import SearchQuery
from .models.records import Record, Ref
from .models.resources import MaterializedPackage


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
        self._closed = False

    def __enter__(self) -> Catalog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def _check_open(self) -> None:
        if self._closed:
            raise CatalogError("Catalog is closed")

    def close(self) -> None:
        self._closed = True

    def get(self, ref: Ref) -> Record:
        """Return a published package record."""
        self._check_open()
        return records.get(self._client, ref)

    def search(self, query: SearchQuery = SearchQuery()) -> tuple[Record, ...]:
        """Find records matching the exact coordinate filters."""
        self._check_open()
        return records.search(self._client, query)

    def dependencies(self, ref: Ref, filters: SearchQuery = SearchQuery()) -> tuple[Record, ...]:
        """Read the immediate dependencies of a published package."""
        self._check_open()
        return records.dependencies(self._client, ref, filters)

    def resolve_local(
        self, record: Record, *, dependencies: tuple[Ref, ...] = (), lockfile: Path | None = None
    ) -> GraphResult:
        """Resolve local project data independently of its publication status."""
        self._check_open()
        return graph.resolve_local(
            self._client, record, dependencies=dependencies, lockfile=lockfile
        )

    def find_dependents(
        self, ref: Ref, *, candidates: SearchQuery, transitive: bool = True
    ) -> tuple[Record, ...]:
        """Find candidates requiring ref, directly or transitively."""
        self._check_open()
        return graph.find_dependents(
            self._client, ref, candidates=candidates, transitive=transitive
        )

    def publish(
        self, record: Record, *, dependencies: tuple[Ref, ...] = (), lockfile: Path | None = None
    ) -> Record:
        """Publish one new package with already published dependencies."""
        self._check_open()
        return publication.publish(self._client, record, dependencies, lockfile=lockfile)

    def publish_file(self, path: str | Path, *, lockfile: Path | None = None) -> Record:
        """Publish one package described by a JSON document."""
        self._check_open()
        return publication.publish_file(self._client, path, lockfile=lockfile)

    def create_lockfile(self, refs: tuple[Ref, ...], path: str | Path) -> Path:
        """Create or update a project-owned Conan lockfile."""
        self._check_open()
        return publication.create_lockfile(self._client, refs, path)

    def materialize(self, ref: Ref, *, with_sources: bool = False) -> MaterializedPackage:
        """Fetch artifacts and optionally the package's pinned SCM sources."""
        self._check_open()
        return materialization.materialize(self._client, ref, with_sources=with_sources)


__all__ = ["Catalog"]
