"""Publication, record queries and ownership of the backend connection."""

from pathlib import Path
from types import TracebackType

from .._internal.validation import normalize_query, normalize_ref
from .models.graph import Dependency
from .models.query import SearchQuery
from .models.records import Record, Ref
from .models.resources import MaterializedPackage
from .protocols import Backend


class Catalog:
    """Manage records and expose graph operations through ``graph``."""

    def __init__(self, backend: Backend) -> None:
        self._backend = backend
        self.graph = backend.graph

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
        return self.get_many((ref,))[0]

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        refs = tuple(normalize_ref(ref) for ref in refs)
        return self._backend.get_many(refs)

    def publish(
        self,
        records: tuple[Record, ...],
        *,
        dependencies: tuple[Dependency, ...] = (),
        lockfile: Path | None = None,
    ) -> None:
        """Save new records and their outgoing dependencies."""
        self._backend.publish(records, dependencies=dependencies, lockfile=lockfile)

    def publish_file(self, path: str | Path, *, lockfile: Path | None = None) -> Record:
        """Publish one package described by JSON; paths are relative to this file."""
        return self._backend.publish_file(Path(path), lockfile=lockfile)

    def create_lockfile(self, refs: tuple[Ref, ...], path: str | Path) -> Path:
        """Capture project dependencies in a Conan-managed lockfile at the caller's path."""
        return self._backend.create_lockfile(refs, Path(path))

    def materialize(
        self, ref: Ref, *, with_sources: bool = False, lockfile: Path | None = None
    ) -> MaterializedPackage:
        """Install artifacts and optionally fetch SCM sources into the backend cache."""
        return self._backend.materialize(
            normalize_ref(ref), with_sources=with_sources, lockfile=lockfile
        )

    def search(self, query: SearchQuery = SearchQuery()) -> tuple[Record, ...]:
        """Return all matching records in deterministic identity order."""
        query = normalize_query(query)
        return self._backend.search(query)

    def find_dependents(
        self, ref: Ref, *, candidates: SearchQuery, transitive: bool = True
    ) -> tuple[Record, ...]:
        """Find candidate records requiring an exact reference, directly or transitively."""
        return self.graph.find_dependents(ref, candidates=candidates, transitive=transitive)


__all__ = ["Catalog"]
