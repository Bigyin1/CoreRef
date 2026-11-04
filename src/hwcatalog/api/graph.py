"""Graph queries with transaction lifetimes managed by the backend."""

from collections.abc import Callable
from contextlib import AbstractContextManager

import networkx as nx

from .._internal.resolution import DependencyResolver, GraphReader
from .._internal.validation import normalize_query, normalize_ref
from .capabilities import FULL_CAPABILITIES, BackendCapabilities
from .enums import Direction, LineageDirection, RelationKind
from .errors import UnsupportedOperation
from .models.graph import GraphResult, Neighbor
from .models.query import SearchQuery
from .models.records import Record, Ref


class CatalogGraph:
    """Read relationships and traverse graphs; the owning Catalog closes the backend.

    Memory and Memgraph use one read transaction per operation. Backends declare
    weaker guarantees through capabilities; this object does not retain a snapshot.
    """

    def __init__(
        self,
        read: Callable[[], AbstractContextManager[GraphReader]],
        capabilities: BackendCapabilities = FULL_CAPABILITIES,
    ) -> None:
        self._read = read
        self._capabilities = capabilities

    def neighbors(
        self,
        ref: Ref,
        relation_kind: RelationKind,
        direction: Direction = Direction.OUTGOING,
        filters: SearchQuery = SearchQuery(),
    ) -> tuple[Neighbor, ...]:
        """Return all matching bindings and neighboring records in one consistent read."""
        ref = normalize_ref(ref)
        filters = normalize_query(filters)
        direction = Direction(direction)
        relation_kind = RelationKind(relation_kind)
        self._require_relation(relation_kind)
        with self._read() as reader:
            return reader.neighbors(ref, relation_kind, direction, filters)

    def dependencies(self, ref: Ref) -> tuple[Neighbor, ...]:
        return self.neighbors(ref, RelationKind.REQUIRES)

    def consumers(self, ref: Ref) -> tuple[Neighbor, ...]:
        return self.neighbors(ref, RelationKind.REQUIRES, Direction.INCOMING)

    def implementations(
        self,
        ref: Ref,
        filters: SearchQuery = SearchQuery(),
    ) -> tuple[Neighbor, ...]:
        return self.neighbors(
            ref,
            RelationKind.IMPLEMENTS,
            Direction.INCOMING,
            filters,
        )

    def dependency_closure(self, ref: Ref, *, resolve_versions: bool = False) -> GraphResult:
        """Return exact dependencies, optionally resolving nearest compatible families.

        A fixed root yields the same result after any valid catalogue extension.
        Equal-depth families follow canonical coordinate order, without backtracking.
        Resolved relationships are derived; published relationships remain exact.
        """
        validated_ref = normalize_ref(ref)
        if resolve_versions and not self._capabilities.version_resolution:
            raise UnsupportedOperation("Backend does not support compatibility resolution")
        with self._read() as reader:
            result = self._traverse(reader, validated_ref, None)
            if resolve_versions:
                return DependencyResolver(reader, validated_ref).resolve(result)
            return result

    def lineage(self, ref: Ref, direction: LineageDirection) -> GraphResult:
        self._require_relation(RelationKind.USED)
        self._require_relation(RelationKind.PRODUCED)
        ref = normalize_ref(ref)
        direction = LineageDirection(direction)
        with self._read() as reader:
            return self._traverse(reader, ref, direction)

    def find_dependents(
        self, ref: Ref, *, candidates: SearchQuery, transitive: bool = True
    ) -> tuple[Record, ...]:
        """Search candidate dependency graphs within one backend read transaction."""
        ref = normalize_ref(ref)
        candidates = normalize_query(candidates)
        with self._read() as reader:
            reader.get_many((ref,))
            matches = []
            for record in reader.search(candidates):
                if record.ref == ref:
                    continue
                if transitive:
                    found = any(
                        r.ref == ref for r in self._traverse(reader, record.ref, None).records
                    )
                else:
                    found = any(n.record.ref == ref for n in self._steps(reader, record.ref, None))
                if found:
                    matches.append(record)
            return tuple(sorted(matches, key=lambda record: record.ref.identity))

    def _require_relation(self, kind: RelationKind) -> None:
        if kind not in self._capabilities.relation_kinds:
            raise UnsupportedOperation(f"Backend does not support {kind.value}")

    def _traverse(
        self, reader: GraphReader, ref: Ref, lineage: LineageDirection | None
    ) -> GraphResult:
        graph: nx.MultiDiGraph[Ref] = nx.MultiDiGraph()
        graph.add_node(ref, record=reader.get_many((ref,))[0])
        for layer in nx.bfs_layers(graph, ref):
            # Load this layer before NetworkX inspects its adjacency for the next.
            for current in layer:
                for neighbor in self._steps(reader, current, lineage):
                    graph.add_node(neighbor.record.ref, record=neighbor.record)
                    graph.add_edge(
                        current,
                        neighbor.record.ref,
                        key=neighbor.relation.to_json(),
                        relation=neighbor.relation,
                    )
        return GraphResult(
            tuple(
                graph.nodes[node]["record"] for node in sorted(graph, key=lambda ref: ref.identity)
            ),
            tuple(
                sorted(
                    (graph.edges[edge]["relation"] for edge in graph.edges),
                    key=lambda relation: relation.to_json(),
                )
            ),
            self._capabilities.consistency,
        )

    @staticmethod
    def _steps(
        reader: GraphReader, ref: Ref, lineage: LineageDirection | None
    ) -> tuple[Neighbor, ...]:
        if lineage is None:
            return reader.neighbors(ref, RelationKind.REQUIRES, Direction.OUTGOING, SearchQuery())
        used = Direction.OUTGOING if lineage == LineageDirection.UPSTREAM else Direction.INCOMING
        produced = (
            Direction.INCOMING if lineage == LineageDirection.UPSTREAM else Direction.OUTGOING
        )
        return (
            *reader.neighbors(ref, RelationKind.USED, used, SearchQuery()),
            *reader.neighbors(ref, RelationKind.PRODUCED, produced, SearchQuery()),
        )


__all__ = ["CatalogGraph"]
