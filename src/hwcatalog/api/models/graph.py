"""Exact dependency edges and immutable graph results."""

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Literal, Self

import networkx as nx

from .records import Record, Ref

# NetworkX generics are provided by stubs, not its runtime classes.
if TYPE_CHECKING:
    type _RefGraph = nx.DiGraph[Ref]
else:
    _RefGraph = nx.DiGraph


@dataclass(frozen=True)
class Dependency:
    """A source record requires an exact target record."""

    source: Ref
    target: Ref

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str) -> Self:
        value = json.loads(payload)
        if set(value) != {"source", "target"}:
            raise ValueError("A dependency contains only source and target")
        return cls(Ref(**value["source"]), Ref(**value["target"]))


@dataclass(frozen=True)
class GraphResult:
    records: tuple[Record, ...]
    dependencies: tuple[Dependency, ...]
    consistency: Literal["snapshot", "live"] = "snapshot"

    def to_networkx(self) -> _RefGraph:
        """Export a detached directed graph with frozen record and dependency attributes."""
        graph: nx.DiGraph[Ref] = nx.DiGraph(consistency=self.consistency)
        graph.add_nodes_from((record.ref, {"record": record}) for record in self.records)
        graph.add_edges_from(
            (edge.source, edge.target, {"dependency": edge}) for edge in self.dependencies
        )
        return graph


__all__ = ["Dependency", "GraphResult"]
