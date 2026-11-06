"""Project dependency resolution, exact edges and detached graph results."""

from dataclasses import dataclass
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


@dataclass(frozen=True)
class GraphResult:
    records: tuple[LocalRecord | Record, ...]
    dependencies: tuple[Dependency, ...]

    def to_networkx(self) -> _RefGraph:
        """Export graph structure with record snapshots and exact dependency attributes."""
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
        catalog: Catalog,
        record: LocalRecord,
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
        return self.catalog._local_package(self.record, self.dependencies, self.lockfile).resolve()

    def create_lockfile(self, path: str | Path | None = None) -> Path:
        """Create or update the dependency lockfile and use it for subsequent resolution."""
        output = path if path is not None else self.lockfile
        if output is None:
            raise CatalogError("Provide a lockfile path in Graph or create_lockfile")
        self.lockfile = self.catalog._local_package(
            self.record, self.dependencies, self.lockfile
        ).create_lockfile(output)
        return self.lockfile


__all__ = ["Dependency", "Graph", "GraphResult"]
