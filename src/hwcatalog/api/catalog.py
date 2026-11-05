"""Publication, record queries and ownership of the backend connection."""

from pathlib import Path
from types import TracebackType

from .._internal.validation import normalize_query, normalize_ref
from .models.graph import GraphResult
from .models.query import SearchQuery
from .models.records import Record, Ref
from .models.resources import MaterializedPackage
from .protocols import Backend


class Catalog:
    """Publish, query, resolve and fetch packages through one backend."""

    def __init__(self, backend: Backend) -> None:
        self._backend = backend

    def __enter__(self) -> Catalog:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._backend.close()

    def get(self, ref: Ref) -> Record:
        return self._backend.get(normalize_ref(ref))

    def dependencies(self, ref: Ref, filters: SearchQuery = SearchQuery()) -> tuple[Record, ...]:
        """Return immediate dependencies, optionally filtered by their record fields."""
        return self._backend.dependencies(ref, filters)

    def resolve_local(
        self, record: Record, *, dependencies: tuple[Ref, ...] = (), lockfile: Path | None = None
    ) -> GraphResult:
        """Resolve local root data regardless of whether its Ref is published."""
        return self._backend.resolve_local(record, dependencies=dependencies, lockfile=lockfile)

    def publish(
        self,
        record: Record,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> Record:
        """Publish one new package requiring already published references."""
        return self._backend.publish(record, dependencies=dependencies, lockfile=lockfile)

    def publish_file(self, path: str | Path, *, lockfile: Path | None = None) -> Record:
        """Publish one package described by JSON; paths are relative to this file."""
        return self._backend.publish_file(Path(path), lockfile=lockfile)

    def create_lockfile(self, refs: tuple[Ref, ...], path: str | Path) -> Path:
        """Capture project dependencies in a Conan-managed lockfile at the caller's path."""
        return self._backend.create_lockfile(refs, Path(path))

    def materialize(self, ref: Ref, *, with_sources: bool = False) -> MaterializedPackage:
        """Fetch a published package and optionally its pinned SCM sources."""
        return self._backend.materialize(normalize_ref(ref), with_sources=with_sources)

    def search(self, query: SearchQuery = SearchQuery()) -> tuple[Record, ...]:
        """Return all matching records in deterministic reference order."""
        query = normalize_query(query)
        return self._backend.search(query)

    def find_dependents(
        self, ref: Ref, *, candidates: SearchQuery, transitive: bool = True
    ) -> tuple[Record, ...]:
        """Find candidate records requiring an exact reference, directly or transitively."""
        return self._backend.find_dependents(ref, candidates=candidates, transitive=transitive)


__all__ = ["Catalog"]
