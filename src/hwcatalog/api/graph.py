"""Exact dependencies and consumers; backends own each read operation."""

from collections.abc import Callable
from contextlib import AbstractContextManager

import networkx as nx

from .._internal.graph import GraphReader
from .._internal.validation import normalize_query, normalize_ref
from .models.graph import Dependency, GraphResult
from .models.query import SearchQuery
from .models.records import Record, Ref


class CatalogGraph:
    """Query dependency graphs; the owning Catalog closes the backend."""

    def __init__(self, read: Callable[[], AbstractContextManager[GraphReader]]) -> None:
        self._read = read

    def dependencies(self, ref: Ref, filters: SearchQuery = SearchQuery()) -> tuple[Record, ...]:
        """Return immediate dependencies in deterministic identity order."""
        ref, filters = normalize_ref(ref), normalize_query(filters)
        with self._read() as reader:
            return reader.dependencies(ref, filters)

    def consumers(self, ref: Ref, filters: SearchQuery = SearchQuery()) -> tuple[Record, ...]:
        """Return records that directly depend on ref."""
        ref, filters = normalize_ref(ref), normalize_query(filters)
        with self._read() as reader:
            return reader.consumers(ref, filters)

    def dependency_closure(self, ref: Ref) -> GraphResult:
        """Return the exact dependency graph, including the selected root."""
        ref = normalize_ref(ref)
        with self._read() as reader:
            return self._traverse(reader, ref)

    def find_dependents(
        self, ref: Ref, *, candidates: SearchQuery, transitive: bool = True
    ) -> tuple[Record, ...]:
        """Filter candidate records by their direct or transitive dependency on ref."""
        ref, candidates = normalize_ref(ref), normalize_query(candidates)
        with self._read() as reader:
            reader.get_many((ref,))
            matches = []
            for record in reader.search(candidates):
                if record.ref == ref:
                    continue
                records = (
                    self._traverse(reader, record.ref).records
                    if transitive
                    else reader.dependencies(record.ref, SearchQuery())
                )
                if any(candidate.ref == ref for candidate in records):
                    matches.append(record)
            return tuple(sorted(matches, key=lambda record: record.ref.identity))

    def _traverse(self, reader: GraphReader, ref: Ref) -> GraphResult:
        graph: nx.DiGraph[Ref] = nx.DiGraph()
        graph.add_node(ref, record=reader.get_many((ref,))[0])
        for layer in nx.bfs_layers(graph, ref):
            for current in layer:
                for record in reader.dependencies(current, SearchQuery()):
                    graph.add_node(record.ref, record=record)
                    graph.add_edge(current, record.ref)
        return GraphResult(
            tuple(
                graph.nodes[node]["record"] for node in sorted(graph, key=lambda ref: ref.identity)
            ),
            tuple(
                sorted(
                    (Dependency(source, target) for source, target in graph.edges),
                    key=lambda edge: edge.to_json(),
                )
            ),
        )


__all__ = ["CatalogGraph"]
