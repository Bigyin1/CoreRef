"""Exact dependency edges and immutable graph results."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import networkx as nx

from .records import Record, Ref

# NetworkX generics are provided by stubs, not its runtime classes.
if TYPE_CHECKING:
    type _RefGraph = nx.DiGraph[Ref]
else:
    _RefGraph = nx.DiGraph


@dataclass(frozen=True, order=True)
class Dependency:
    """A source record requires an exact target record."""

    source: Ref
    target: Ref


@dataclass(frozen=True)
class GraphResult:
    records: tuple[Record, ...]
    dependencies: tuple[Dependency, ...]

    def to_networkx(self) -> _RefGraph:
        """Export a detached directed graph with frozen record and dependency attributes."""
        graph: nx.DiGraph[Ref] = nx.DiGraph()
        graph.add_nodes_from((record.ref, {"record": record}) for record in self.records)
        graph.add_edges_from(
            (edge.source, edge.target, {"dependency": edge}) for edge in self.dependencies
        )
        return graph


__all__ = ["Dependency", "GraphResult"]
