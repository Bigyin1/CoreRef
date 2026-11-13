"""Resolved dependencies, a working local root, ordering and lockfiles."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from .errors import CatalogError, InvalidPackage
from .query import SearchQuery
from .records import InstalledRecord, LocalRecord, Record, Ref

# NetworkX generics are provided by stubs, not its runtime classes.
if TYPE_CHECKING:
    from .._internal.package import LocalPackage
    from .catalog import Catalog

    _RefGraph = nx.DiGraph[Ref]
else:
    _RefGraph = nx.DiGraph


@dataclass(init=False)
class Graph:
    """Resolve a package; use an existing lockfile and retain a missing path for later saving."""

    _records: tuple[LocalRecord | Record, ...]
    _dependencies: tuple[tuple[Ref, Ref], ...]

    def __init__(
        self,
        catalog: Catalog,
        record: LocalRecord | Ref,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> None:
        lockfile = lockfile.expanduser().absolute() if lockfile is not None else None
        seed = lockfile if lockfile is not None and lockfile.exists() else None
        self._catalog = catalog
        self._local: LocalPackage | None = None
        if isinstance(record, Ref):
            if dependencies:
                raise InvalidPackage("Published graph requirements come from its recipe")
            self._package = catalog._package(record).resolved_graph(seed)
        else:
            self._local = catalog._local_package(record, dependencies, seed)
            self._package = self._local.resolved
        self._lockfile = lockfile
        self._records, self._dependencies = self._package.result
        self._root_ref = self._package.record.ref
        self._index = {record.ref: record for record in self._records}
        self._package.resolve_ref(self._root_ref)
        self._requirements = tuple(
            target for source, target in self._dependencies if source == self._package.record.ref
        )
        self._orders: dict[tuple[Ref, tuple[Ref, ...]], tuple[Ref, ...]] = {}

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    @property
    def record(self) -> LocalRecord | Record:
        """Return the working local root, or immutable metadata of a published root."""
        record = self._package.record
        if record.ref != self._root_ref:
            raise InvalidPackage("Local root coordinates changed; create a new Graph")
        return record

    @property
    def records(self) -> tuple[LocalRecord | Record, ...]:
        """Return records with the same working local root as record and info()."""
        _ = self.record
        return self._records

    def info(self, ref: Ref) -> LocalRecord | Record:
        """Read resolved metadata or the working local root, without package I/O."""
        ref = self._package.resolve_ref(ref)
        _ = self.record
        return self._index[ref]

    def dependencies(
        self,
        ref: Ref | None = None,
        *,
        transitive: bool = False,
        filters: SearchQuery | None = None,
    ) -> tuple[Record, ...]:
        """Read dependencies without I/O; transitive results exclude the root, leaves first."""
        selected = self.record.ref if ref is None else self._package.resolve_ref(ref)
        query = SearchQuery() if filters is None else filters
        records = []
        targets = self.ordered_refs(selected)[:-1] if transitive else self._neighbors[selected]
        for target in targets:
            record = self._index[target]
            if isinstance(record, Record) and query.matches(record):
                records.append(record)
        return tuple(records)

    @cached_property
    def _neighbors(self) -> dict[Ref, tuple[Ref, ...]]:
        neighbors: dict[Ref, list[Ref]] = {record.ref: [] for record in self._records}
        for source, target in self._dependencies:
            neighbors[source].append(target)
        return {ref: tuple(targets) for ref, targets in neighbors.items()}

    def _publish(self) -> InstalledRecord:
        if self._local is None:
            raise InvalidPackage(
                "Only a local graph can be published; use a LocalRecord for a new package"
            )
        _ = self.record
        return self._local.publish(resolved=self._package.graph)

    @property
    def requirements(self) -> tuple[Ref, ...]:
        return self._requirements

    @property
    def lockfile(self) -> Path | None:
        return self._lockfile

    def create_lockfile(self, path: str | Path | None = None) -> Path:
        """Save the resolved graph through Conan; remember the path only after success."""
        output = path if path is not None else self.lockfile
        if output is None:
            raise CatalogError("Provide a lockfile path in Graph or create_lockfile")
        _ = self.record
        self._lockfile = self._package.create_lockfile(output)
        return self._lockfile

    def get(
        self,
        refs: tuple[Ref, ...] | None = None,
        *,
        filters: SearchQuery | None = None,
    ) -> tuple[InstalledRecord, ...]:
        """Fetch selected files, or all registry/editable nodes in dependency-first order."""
        return self._get(refs, filters, with_sources=False)

    def get_with_sources(
        self,
        refs: tuple[Ref, ...] | None = None,
        *,
        filters: SearchQuery | None = None,
    ) -> tuple[InstalledRecord, ...]:
        """Fetch files and sources; omitted refs select registry/editable nodes, leaves first."""
        return self._get(refs, filters, with_sources=True)

    def _get(
        self, refs: tuple[Ref, ...] | None, filters: SearchQuery | None, *, with_sources: bool
    ) -> tuple[InstalledRecord, ...]:
        if refs is None:
            refs = tuple(
                record.ref
                for record in self.ordered_records(self._root_ref)
                if isinstance(record, Record)
            )
        if filters is not None:
            refs = tuple(ref for ref in refs if filters.matches(self.info(ref)))
        return self._package.get_with_sources(refs) if with_sources else self._package.get(refs)

    @property
    def networkx(self) -> _RefGraph:
        """Expose the cached read-only topology; use .copy() for a working graph."""
        _ = self.record
        return self._networkx

    @cached_property
    def _networkx(self) -> _RefGraph:
        """Build topology once; use .copy() when a mutable working graph is needed."""
        graph: nx.DiGraph[Ref] = nx.DiGraph()
        graph.add_nodes_from(self._index)
        graph.add_edges_from(self._dependencies)
        nx.freeze(graph)
        return graph

    def ordered_refs(self, root: Ref, *, stop_at: tuple[Ref, ...] = ()) -> tuple[Ref, ...]:
        """Order reachable dependencies first, treating stop_at references as leaves."""
        root = self._package.resolve_ref(root)
        stops = tuple(self._package.resolve_ref(ref) for ref in stop_at)
        key = (root, tuple(sorted(set(stops))))
        if key not in self._orders:
            self._orders[key] = self._order(root, key[1])
        return self._orders[key]

    def ordered_records(
        self, root: Ref, *, stop_at: tuple[Ref, ...] = ()
    ) -> tuple[LocalRecord | Record, ...]:
        """Return records in the same dependency-first order as ordered_refs."""
        return tuple(self.info(ref) for ref in self.ordered_refs(root, stop_at=stop_at))

    def _order(self, root: Ref, stop_at: tuple[Ref, ...]) -> tuple[Ref, ...]:
        if root not in self.networkx:
            raise InvalidPackage("Unknown dependency graph root")
        stops = frozenset(stop_at)
        if root in stops or not stops.issubset(self.networkx):
            raise InvalidPackage("Stop references must be dependencies of the root")
        graph = nx.subgraph_view(
            self.networkx, filter_edge=lambda source, _target: source not in stops
        )
        reachable = {root, *nx.descendants(graph, root)}
        if not stops.issubset(reachable):
            raise InvalidPackage("A stop reference is hidden or unreachable from the root")
        return tuple(
            nx.lexicographical_topological_sort(graph.subgraph(reachable).reverse(copy=False))
        )


__all__ = ["Graph"]
