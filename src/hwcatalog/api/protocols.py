"""Storage operations used by Catalog."""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .graph import CatalogGraph
    from .models.graph import Dependency
    from .models.query import SearchQuery
    from .models.records import Record, Ref


@runtime_checkable
class JSONSerializable(Protocol):
    """A consumer-owned payload that can serialize itself to a JSON document."""

    def to_json(self) -> str: ...


class Backend(Protocol):
    """Persist publications and provide records and graph queries.

    Each operation owns its reads and returns immutable domain values.
    The PoC assumes one publisher per reference; callers coordinate writes.
    """

    graph: CatalogGraph

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        """Preserve input order and duplicates; raise NotFound if any reference is absent."""
        ...

    def search(self, query: SearchQuery) -> tuple[Record, ...]:
        """Return all exact AND-matches in deterministic identity order; an empty tuple if none."""
        ...

    def publish(
        self, records: tuple[Record, ...], *, dependencies: tuple[Dependency, ...] = ()
    ) -> None:
        """Save a batch of new records and dependencies; existing refs raise IdentityConflict.

        There is no publication tracking or retry guarantee in the PoC API.
        """
        ...

    def close(self) -> None:
        """Release backend resources; repeated closure must be safe."""
        ...


__all__ = ["Backend", "JSONSerializable"]
