"""In-memory reference storage for records and exact dependencies."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from ...api.errors import NotFound
from ...api.graph import CatalogGraph
from ...api.models.graph import Dependency
from ...api.models.query import SearchQuery
from ...api.models.records import Record, Ref
from ..validation import normalize_dependency, normalize_record, validate


class MemoryReader:
    def __init__(self, records: dict[Ref, Record], dependencies: tuple[Dependency, ...]) -> None:
        self.records = records
        self.edges = dependencies

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        try:
            return tuple(self.records[ref] for ref in refs)
        except KeyError as exc:
            raise NotFound(str(exc.args[0])) from exc

    def search(self, query: SearchQuery) -> tuple[Record, ...]:
        return self._matching(tuple(self.records.values()), query)

    def dependencies(self, ref: Ref, filters: SearchQuery) -> tuple[Record, ...]:
        self.get_many((ref,))
        return self._matching(
            tuple(self.records[edge.target] for edge in self.edges if edge.source == ref), filters
        )

    def consumers(self, ref: Ref, filters: SearchQuery) -> tuple[Record, ...]:
        self.get_many((ref,))
        return self._matching(
            tuple(self.records[edge.source] for edge in self.edges if edge.target == ref), filters
        )

    @staticmethod
    def _matching(records: tuple[Record, ...], filters: SearchQuery) -> tuple[Record, ...]:
        return tuple(
            sorted((r for r in records if filters.matches(r)), key=lambda r: r.ref.identity)
        )


class MemoryBackend:
    def __init__(self) -> None:
        self._records: dict[Ref, Record] = {}
        self._dependencies: tuple[Dependency, ...] = ()
        self._lock = threading.RLock()
        self.graph = CatalogGraph(self._read)

    @contextmanager
    def _read(self) -> Iterator[MemoryReader]:
        with self._lock:
            yield MemoryReader(self._records, self._dependencies)

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        with self._read() as reader:
            return reader.get_many(refs)

    def search(self, query: SearchQuery) -> tuple[Record, ...]:
        with self._read() as reader:
            return reader.search(query)

    def publish(
        self, records: tuple[Record, ...], *, dependencies: tuple[Dependency, ...] = ()
    ) -> None:
        records = tuple(normalize_record(record) for record in records)
        dependencies = tuple(normalize_dependency(edge) for edge in dependencies)
        with self._lock:
            validate(records, dependencies, self._records.keys())
            self._records.update((record.ref, record) for record in records)
            self._dependencies += dependencies

    def close(self) -> None:
        """No external resources are retained by the reference backend."""
