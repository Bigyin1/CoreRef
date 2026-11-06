"""Project dependency resolution, exact edges and immutable graph results."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from .errors import CatalogError
from .protocols import RefCatalog
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


class Graph:
    """One project's root, direct dependencies and optional project-owned lockfile."""

    def __init__(
        self,
        catalog: RefCatalog,
        record: Record,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> None:
        self.catalog = catalog
        self.record = record
        self.dependencies = dependencies
        self.lockfile = lockfile

    def resolve(self) -> GraphResult:
        """Resolve the local root and published dependencies using this project's lockfile."""
        from .._internal.conan import graph

        return graph.resolve_local(
            self.catalog._client,
            self.record,
            dependencies=self.dependencies,
            lockfile=self.lockfile,
            catalog=self.catalog,
        )

    def create_lockfile(self, path: str | Path | None = None) -> Path:
        """Create or update the dependency lockfile and use it for subsequent resolution."""
        from .._internal.conan import graph

        output = path if path is not None else self.lockfile
        if output is None:
            raise CatalogError("Provide a lockfile path in Graph or create_lockfile")
        self.lockfile = graph.create_lockfile(self.catalog._client, self.dependencies, output)
        return self.lockfile


__all__ = ["Dependency", "Graph", "GraphResult"]
