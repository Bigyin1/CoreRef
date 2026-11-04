"""Record queries within one backend-owned read operation."""

from typing import Protocol

from ..api.models.query import SearchQuery
from ..api.models.records import Record, Ref


class GraphReader(Protocol):
    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]: ...

    def search(self, query: SearchQuery) -> tuple[Record, ...]: ...

    def dependencies(self, ref: Ref, filters: SearchQuery) -> tuple[Record, ...]: ...

    def consumers(self, ref: Ref, filters: SearchQuery) -> tuple[Record, ...]: ...
