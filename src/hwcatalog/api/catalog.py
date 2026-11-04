"""Publication, record queries and ownership of the backend connection."""

from types import TracebackType

from .._internal.validation import normalize_query, normalize_ref
from .models.graph import Dependency
from .models.query import SearchQuery
from .models.records import Record, Ref
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
        self, records: tuple[Record, ...], *, dependencies: tuple[Dependency, ...] = ()
    ) -> None:
        """Save new records and their outgoing dependencies."""
        self._backend.publish(records, dependencies=dependencies)

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
