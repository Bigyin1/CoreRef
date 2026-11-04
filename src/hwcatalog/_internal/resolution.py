"""Greedily resolve exact dependencies in a reproducible order without backtracking."""

from dataclasses import replace
from typing import Protocol

import networkx as nx

from ..api.enums import Direction, RelationKind
from ..api.errors import DependencyCycle, VersionConflict
from ..api.models.graph import GraphResult, Neighbor, Relation
from ..api.models.query import SearchQuery
from ..api.models.records import Record, Ref


class GraphReader(Protocol):
    """Queries bound to a single backend transaction during a graph operation."""

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]: ...

    def search(self, query: SearchQuery) -> tuple[Record, ...]: ...

    def neighbors(
        self,
        ref: Ref,
        kind: RelationKind,
        direction: Direction,
        filters: SearchQuery,
    ) -> tuple[Neighbor, ...]: ...


class DependencyResolver:
    """Reduce one compatible family at a time, pruning obsolete branches after each choice."""

    def __init__(self, reader: GraphReader, root: Ref) -> None:
        self._reader = reader
        self._root = root
        self._compatibility: nx.DiGraph[Ref] = nx.DiGraph()
        self._loaded: set[Ref] = set()

    def resolve(self, result: GraphResult) -> GraphResult:
        while True:
            groups = self._groups(result)
            replacements = self._next_replacements(groups)
            if not replacements:
                break
            result = self._replace(result, replacements)
        if groups:
            versions = groups[0]
            ref = versions[0]
            reason = "Root release is pinned" if self._root in versions else "Incompatible versions"
            raise VersionConflict(f"{reason} for {ref.name}: {versions!r}")
        if not nx.is_directed_acyclic_graph(result.to_networkx()):
            raise DependencyCycle("Cycle after resolving dependency versions")
        return result

    def _groups(self, result: GraphResult) -> tuple[tuple[Ref, ...], ...]:
        """Order families by their nearest current root distance, then canonical coordinates."""
        distances = nx.single_source_shortest_path_length(result.to_networkx(), self._root)
        groups: dict[str, list[Ref]] = {}
        ordered = sorted(distances, key=lambda ref: (distances[ref], ref.family, ref.identity))
        for ref in ordered:
            groups.setdefault(ref.family, []).append(ref)
        return tuple(tuple(versions) for versions in groups.values() if len(versions) > 1)

    def _next_replacements(self, groups: tuple[tuple[Ref, ...], ...]) -> dict[Ref, Ref]:
        for versions in groups:
            selected = self._newest(versions)
            if selected is None:
                continue
            if self._root in versions and selected != self._root:
                continue
            return {ref: selected for ref in versions if ref != selected}
        return {}

    def _newest(self, versions: tuple[Ref, ...]) -> Ref | None:
        for ref in versions:
            self._load_chain(ref)
        for candidate in versions:
            if all(nx.has_path(self._compatibility, candidate, other) for other in versions):
                return candidate
        return None

    def _load_chain(self, ref: Ref) -> None:
        while ref not in self._loaded:
            self._loaded.add(ref)
            self._compatibility.add_node(ref)
            previous = self._reader.neighbors(
                ref, RelationKind.BACKWARD_COMPATIBLE_WITH, Direction.OUTGOING, SearchQuery()
            )
            if not previous:
                return
            parent = previous[0].record.ref
            self._compatibility.add_edge(ref, parent)
            ref = parent

    def _replace(self, result: GraphResult, replacements: dict[Ref, Ref]) -> GraphResult:
        relations: dict[str, Relation] = {}
        for edge in result.relations:
            if edge.source not in replacements:
                resolved = replace(edge, target=replacements.get(edge.target, edge.target))
                relations[resolved.to_json()] = resolved
        graph: nx.DiGraph[Ref] = nx.DiGraph()
        graph.add_node(self._root)
        graph.add_edges_from((edge.source, edge.target) for edge in relations.values())
        reachable = nx.descendants(graph, self._root) | {self._root}
        return GraphResult(
            tuple(record for record in result.records if record.ref in reachable),
            tuple(
                relations[key]
                for key in sorted(relations)
                if relations[key].source in reachable and relations[key].target in reachable
            ),
            result.consistency,
        )
