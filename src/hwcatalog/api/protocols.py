"""Storage operations used by Catalog."""

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .graph import CatalogGraph
    from .models.query import SearchQuery
    from .models.records import Record, Ref
    from .models.resources import MaterializedPackage


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
        self,
        record: Record,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> Record:
        """Publish one new package; dependencies must already exist.

        There is no publication tracking or retry guarantee in the PoC API.
        """
        ...

    def publish_file(self, path: Path, *, lockfile: Path | None = None) -> Record:
        """Publish a single-package JSON document with optional source and files."""
        ...

    def create_lockfile(self, refs: tuple[Ref, ...], path: Path) -> Path:
        """Write a project lockfile using Conan; Memory does not support this operation."""
        ...

    def materialize(
        self, ref: Ref, *, with_sources: bool = False, lockfile: Path | None = None
    ) -> MaterializedPackage:
        """Provide artifacts; source_path is populated only when sources are requested."""
        ...

    def close(self) -> None:
        """Release backend resources; repeated closure must be safe."""
        ...


__all__ = ["Backend", "JSONSerializable"]
