"""Graph bindings and immutable read results."""

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Literal, Self

import networkx as nx
from frozendict import deepfreeze

from ..enums import RelationKind
from ..types import JSON
from .records import Record, Ref

# NetworkX generics exist in type stubs, but its runtime classes are not subscriptable.
if TYPE_CHECKING:
    type _RefGraph = nx.MultiDiGraph[Ref]
else:
    _RefGraph = nx.MultiDiGraph


@dataclass(frozen=True)
class Relation:
    source: Ref
    target: Ref
    kind: RelationKind
    alias: str | None = None
    ordinal: int | None = None
    metadata: Mapping[str, JSON] = field(default_factory=dict)

    def to_json(self) -> str:
        """Marshal this binding as stable JSON, including its metadata."""
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_json(cls, payload: str) -> Self:
        """Restore references, relation kind and immutable metadata."""
        value = json.loads(payload)
        return cls(
            Ref(**value["source"]),
            Ref(**value["target"]),
            RelationKind(value["kind"]),
            value["alias"],
            value["ordinal"],
            deepfreeze(value["metadata"]),
        )


@dataclass(frozen=True)
class Neighbor:
    relation: Relation
    record: Record


@dataclass(frozen=True)
class GraphResult:
    records: tuple[Record, ...]
    relations: tuple[Relation, ...]
    consistency: Literal["snapshot", "live"] = "snapshot"

    def to_networkx(self) -> _RefGraph:
        """Export a detached, mutable graph with original directions and all bindings.

        Nodes are references with a frozen ``record`` attribute; edge keys are
        canonical relations, with the frozen value in the ``relation`` attribute.
        Editing the graph never changes this result or the catalog.
        """
        graph: nx.MultiDiGraph[Ref] = nx.MultiDiGraph(consistency=self.consistency)
        graph.add_nodes_from((record.ref, {"record": record}) for record in self.records)
        for relation in self.relations:
            graph.add_edge(
                relation.source,
                relation.target,
                key=relation.to_json(),
                relation=relation,
            )
        return graph


__all__ = ["GraphResult", "Neighbor", "Relation"]
