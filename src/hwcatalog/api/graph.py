"""Resolved project snapshots, dependency ordering and project-owned lockfiles."""

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from .errors import CatalogError
from .records import LocalRecord, Record, Ref

# NetworkX generics are provided by stubs, not its runtime classes.
if TYPE_CHECKING:
    from .catalog import Catalog

    type _RefGraph = nx.DiGraph[Ref]
else:
    _RefGraph = nx.DiGraph


@dataclass(frozen=True, order=True)
class Dependency:
    """A source record requires an exact target record."""

    source: Ref
    target: Ref


@dataclass(init=False)
class Graph:
    """One resolved project with records, edges, traversals and lockfile operations."""

    records: tuple[LocalRecord | Record, ...]
    dependencies: tuple[Dependency, ...]

    def __init__(
        self,
        catalog: Catalog,
        record: LocalRecord,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> None:
        self.catalog = catalog
        self._package = catalog._local_package(record, dependencies, lockfile)
        self.record = self._package.record
        self.requirements = self._package.dependencies
        self.lockfile = lockfile
        self.records, self.dependencies = self._package.result
        self._orders: dict[tuple[Ref, tuple[Ref, ...]], tuple[Ref, ...]] = {}

    def create_lockfile(self, path: str | Path | None = None) -> Path:
        """Save the resolved graph through Conan; remember the path only after success."""
        output = path if path is not None else self.lockfile
        if output is None:
            raise CatalogError("Provide a lockfile path in Graph or create_lockfile")
        self.lockfile = self._package.create_lockfile(output)
        return self.lockfile

    @cached_property
    def networkx(self) -> _RefGraph:
        """Build topology once; use .copy() when a mutable working graph is needed."""
        graph: nx.DiGraph[Ref] = nx.DiGraph()
        graph.add_nodes_from((record.ref, {"record": record}) for record in self.records)
        graph.add_edges_from(
            (edge.source, edge.target, {"dependency": edge}) for edge in self.dependencies
        )
        nx.freeze(graph)
        return graph

    def ordered_refs(self, root: Ref, *, stop_at: tuple[Ref, ...] = ()) -> tuple[Ref, ...]:
        """Order reachable dependencies first, treating stop_at references as leaves."""
        key = (root, tuple(sorted(set(stop_at))))
        if key not in self._orders:
            self._orders[key] = self._order(root, key[1])
        return self._orders[key]

    def ordered_records(
        self, root: Ref, *, stop_at: tuple[Ref, ...] = ()
    ) -> tuple[LocalRecord | Record, ...]:
        """Return records in the same dependency-first order as ordered_refs."""
        return tuple(self._records_by_ref[ref] for ref in self.ordered_refs(root, stop_at=stop_at))

    @cached_property
    def _records_by_ref(self) -> dict[Ref, LocalRecord | Record]:
        return {record.ref: record for record in self.records}

    def _order(self, root: Ref, stop_at: tuple[Ref, ...]) -> tuple[Ref, ...]:
        if root not in self.networkx:
            raise ValueError("Unknown dependency graph root")
        stops = frozenset(stop_at)
        if root in stops or not stops.issubset(self.networkx):
            raise ValueError("Stop references must be dependencies of the root")
        graph = nx.subgraph_view(
            self.networkx, filter_edge=lambda source, _target: source not in stops
        )
        reachable = {root, *nx.descendants(graph, root)}
        if not stops.issubset(reachable):
            raise ValueError("A stop reference is hidden or unreachable from the root")
        return tuple(
            nx.lexicographical_topological_sort(graph.subgraph(reachable).reverse(copy=False))
        )


__all__ = ["Dependency", "Graph"]
