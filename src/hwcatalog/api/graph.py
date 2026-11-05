"""Backend-independent queries returning domain graph snapshots."""

from pathlib import Path
from typing import Protocol

from .models.graph import GraphResult
from .models.query import SearchQuery
from .models.records import Record, Ref


class CatalogGraph(Protocol):
    """Resolve dependencies with the owning backend; native Conan objects stay internal."""

    def dependencies(self, ref: Ref, filters: SearchQuery = SearchQuery()) -> tuple[Record, ...]:
        """Return immediate dependencies in deterministic reference order."""
        ...

    def consumers(self, ref: Ref, filters: SearchQuery = SearchQuery()) -> tuple[Record, ...]:
        """Find immediate consumers; Conan checks candidate requirements."""
        ...

    def dependency_closure(self, ref: Ref, *, lockfile: Path | None = None) -> GraphResult:
        """Resolve a published root and its complete dependency graph."""
        ...

    def resolve_local(
        self, record: Record, *, dependencies: tuple[Ref, ...] = (), lockfile: Path | None = None
    ) -> GraphResult:
        """Resolve a local root and published dependencies without publishing the root."""
        ...

    def find_dependents(
        self, ref: Ref, *, candidates: SearchQuery, transitive: bool = True
    ) -> tuple[Record, ...]:
        """Filter candidates by their direct or transitive dependency on ref."""
        ...


__all__ = ["CatalogGraph"]
