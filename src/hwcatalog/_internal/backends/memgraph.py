"""Simple Memgraph storage for the prototype, using the Neo4j Bolt driver."""

from collections.abc import Iterator
from contextlib import contextmanager

from neo4j import GraphDatabase, Session, Transaction
from neo4j.exceptions import DriverError, Neo4jError

from ...api.capabilities import FULL_CAPABILITIES
from ...api.enums import Direction, RelationKind
from ...api.errors import BackendUnavailable, NotFound
from ...api.graph import CatalogGraph
from ...api.models.graph import Neighbor, Relation
from ...api.models.query import SearchQuery
from ...api.models.records import Record, Ref
from ..validation import normalize_record, normalize_relation, validate

_SEARCH_FIELDS = ("name", "version", "kind", "namespace")
_STORAGE_ERRORS = (DriverError, Neo4jError, OSError)


class MemgraphReader:
    def __init__(self, tx: Transaction) -> None:
        self.tx = tx
        self._records: dict[Ref, Record] = {}

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        found = self.existing(refs)
        try:
            return tuple(found[ref] for ref in refs)
        except KeyError as exc:
            raise NotFound(str(exc.args[0])) from exc

    def existing(self, refs: tuple[Ref, ...]) -> dict[Ref, Record]:
        missing = list(dict.fromkeys(ref.identity for ref in refs if ref not in self._records))
        if missing:
            rows = self.tx.run(
                "UNWIND $keys AS key MATCH (n:HCRecord {key: key}) RETURN n.payload AS p",
                keys=missing,
            )
            for row in rows:
                record = Record.from_json(str(row["p"]))
                self._records[record.ref] = record
        return {ref: self._records[ref] for ref in refs if ref in self._records}

    def search(self, query: SearchQuery) -> tuple[Record, ...]:
        where, params = self._filters(query)
        rows = self.tx.run(
            f"MATCH (n:HCRecord) {where} RETURN n.payload AS p ORDER BY n.key",
            parameters=params,
        )
        return tuple(Record.from_json(str(row["p"])) for row in rows)

    def neighbors(
        self,
        ref: Ref,
        kind: RelationKind,
        direction: Direction,
        filters: SearchQuery,
    ) -> tuple[Neighbor, ...]:
        self.get_many((ref,))
        # kind/direction are enums, never user-controlled Cypher fragments.
        pattern = (
            f"(s)-[r:{kind.value}]->(n:HCRecord)"
            if direction == Direction.OUTGOING
            else f"(s)<-[r:{kind.value}]-(n:HCRecord)"
        )
        where, params = self._filters(filters)
        rows = self.tx.run(
            f"MATCH (s:HCRecord {{key: $key}}) MATCH {pattern} {where} "
            "RETURN r.payload AS e ORDER BY r.payload",
            parameters={**params, "key": ref.identity},
        )
        edges = tuple(Relation.from_json(str(row["e"])) for row in rows)
        refs = tuple(
            edge.target if direction == Direction.OUTGOING else edge.source for edge in edges
        )
        records = self.get_many(refs)
        return tuple(Neighbor(edge, record) for edge, record in zip(edges, records, strict=True))

    @staticmethod
    def _filters(query: SearchQuery) -> tuple[str, dict[str, str]]:
        params = query.criteria()
        conditions = [f"n.{key} = ${key}" for key in _SEARCH_FIELDS if key in params]
        return ("WHERE " + " AND ".join(conditions) if conditions else ""), params


class MemgraphBackend:
    """Prototype storage; the caller coordinates publication of each release."""

    capabilities = FULL_CAPABILITIES

    def __init__(
        self, uri: str = "bolt://localhost:17687", *, auth: tuple[str, str] | None = None
    ) -> None:
        self._driver = GraphDatabase.driver(uri, auth=auth)
        self.graph = CatalogGraph(self._read)
        try:
            self._initialize()
        except BackendUnavailable:
            self._driver.close()
            raise

    def _initialize(self) -> None:
        with self._session() as session:
            for field in ("key", *_SEARCH_FIELDS):
                session.run(f"CREATE INDEX ON :HCRecord({field})").consume()
            session.run("CREATE INDEX ON :HCRecord").consume()

    @contextmanager
    def _session(self) -> Iterator[Session]:
        try:
            with self._driver.session() as session:
                yield session
        except _STORAGE_ERRORS as exc:
            raise BackendUnavailable("Memgraph operation failed") from exc

    @contextmanager
    def _read(self) -> Iterator[MemgraphReader]:
        with self._session() as session, session.begin_transaction() as tx:
            yield MemgraphReader(tx)

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        with self._read() as reader:
            return reader.get_many(refs)

    def search(self, query: SearchQuery) -> tuple[Record, ...]:
        with self._read() as reader:
            return reader.search(query)

    def publish(self, records: tuple[Record, ...], *, relations: tuple[Relation, ...] = ()) -> None:
        records = tuple(normalize_record(record) for record in records)
        relations = tuple(normalize_relation(edge) for edge in relations)
        with self._session() as session, session.begin_transaction() as tx:
            refs = tuple(record.ref for record in records) + tuple(
                edge.target for edge in relations
            )
            existing = MemgraphReader(tx).existing(refs)
            validate(records, relations, existing.keys())
            self._insert(tx, records, relations)
            tx.commit()

    def close(self) -> None:
        self._driver.close()

    @staticmethod
    def _properties(record: Record) -> dict[str, str]:
        return {
            "key": record.ref.identity,
            "payload": record.to_json(),
            "name": record.ref.name,
            "version": record.ref.version,
            "kind": record.ref.kind,
            "namespace": record.ref.namespace,
        }

    def _insert(
        self, tx: Transaction, records: tuple[Record, ...], relations: tuple[Relation, ...]
    ) -> None:
        tx.run(
            "UNWIND $records AS properties CREATE (n:HCRecord) SET n = properties",
            records=[self._properties(record) for record in records],
        ).consume()
        for kind in RelationKind:
            edges = [
                {
                    "source": edge.source.identity,
                    "target": edge.target.identity,
                    "payload": edge.to_json(),
                }
                for edge in relations
                if edge.kind == kind
            ]
            if edges:
                tx.run(
                    "UNWIND $edges AS edge "
                    "MATCH (s:HCRecord {key: edge.source}), (t:HCRecord {key: edge.target}) "
                    f"CREATE (s)-[r:{kind.value}]->(t) SET r.payload = edge.payload",
                    edges=edges,
                ).consume()
