"""Reference storage. Its lock models a single atomic storage transaction."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from ...api.capabilities import FULL_CAPABILITIES
from ...api.enums import Direction, RelationKind
from ...api.errors import NotFound
from ...api.graph import CatalogGraph
from ...api.models.graph import Neighbor, Relation
from ...api.models.query import SearchQuery
from ...api.models.records import Record, Ref
from ..validation import normalize_record, normalize_relation, validate


class MemoryReader:
    def __init__(self, records: dict[Ref, Record], relations: tuple[Relation, ...]) -> None:
        self.records = records
        self.relations = relations

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        try:
            return tuple(self.records[ref] for ref in refs)
        except KeyError as exc:
            raise NotFound(str(exc.args[0])) from exc

    def search(self, query: SearchQuery) -> tuple[Record, ...]:
        return tuple(
            sorted(
                (r for r in self.records.values() if query.matches(r)),
                key=lambda r: r.ref.identity,
            )
        )

    def neighbors(
        self,
        ref: Ref,
        kind: RelationKind,
        direction: Direction,
        filters: SearchQuery,
    ) -> tuple[Neighbor, ...]:
        self.get_many((ref,))
        result = []
        for edge in self.relations:
            source, target = (
                (edge.source, edge.target)
                if direction == Direction.OUTGOING
                else (edge.target, edge.source)
            )
            if source == ref and edge.kind == kind and filters.matches(self.records[target]):
                result.append(Neighbor(edge, self.records[target]))
        return tuple(sorted(result, key=lambda n: n.relation.to_json()))


class MemoryBackend:
    capabilities = FULL_CAPABILITIES

    def __init__(self) -> None:
        self._records: dict[Ref, Record] = {}
        self._relations: tuple[Relation, ...] = ()
        self._lock = threading.RLock()
        self.graph = CatalogGraph(self._read)

    @contextmanager
    def _read(self) -> Iterator[MemoryReader]:
        with self._lock:
            yield MemoryReader(self._records, self._relations)

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        with self._read() as reader:
            return reader.get_many(refs)

    def search(self, query: SearchQuery) -> tuple[Record, ...]:
        with self._read() as reader:
            return reader.search(query)

    def publish(self, records: tuple[Record, ...], *, relations: tuple[Relation, ...] = ()) -> None:
        records = tuple(normalize_record(record) for record in records)
        relations = tuple(normalize_relation(edge) for edge in relations)
        with self._lock:
            validate(records, relations, self._records.keys())
            self._records.update((record.ref, record) for record in records)
            self._relations += relations

    def close(self) -> None:
        """Reference backend owns no external resources."""
