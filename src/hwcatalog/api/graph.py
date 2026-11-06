"""Resolved local and published packages, dependency ordering and lockfiles."""

from dataclasses import dataclass, replace
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx

from .errors import CatalogError, InvalidPackage
from .records import InstalledRecord, LocalRecord, Record, Ref

# NetworkX generics are provided by stubs, not its runtime classes.
if TYPE_CHECKING:
    from .catalog import Catalog
    from .workspace import Workspace

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
    """One resolved local or published package with records, ordering and lockfiles."""

    _records: tuple[LocalRecord | Record, ...]
    _dependencies: tuple[Dependency, ...]

    def __init__(
        self,
        catalog: Catalog,
        record: LocalRecord | Ref,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
        workspace: Workspace | None = None,
    ) -> None:
        self._catalog = catalog
        if workspace is not None:
            dependencies = workspace._pin(dependencies)
            if isinstance(record, Ref):
                record = workspace._pin((record,))[0]
        if isinstance(record, Ref):
            if dependencies:
                raise InvalidPackage("Published graph requirements come from its recipe")
            self._package = catalog._package(record).resolved_graph(lockfile)
        else:
            self._package = catalog._local_package(record, dependencies, lockfile).resolved
        self._lockfile = lockfile
        self._records, self._dependencies = self._package.result
        published = (item for item in self._records if isinstance(item, Record))
        self._editables = workspace._select(published) if workspace is not None else {}
        self._requirements = tuple(
            edge.target for edge in self._dependencies if edge.source == self._package.record.ref
        )
        self._orders: dict[tuple[Ref, tuple[Ref, ...]], tuple[Ref, ...]] = {}

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    @property
    def record(self) -> LocalRecord | Record:
        """Return immutable stored metadata or a detached editable local description."""
        record = self._package.record
        return replace(record) if isinstance(record, LocalRecord) else record

    @property
    def records(self) -> tuple[LocalRecord | Record, ...]:
        """Expose records without sharing the mutable local root."""
        return tuple(
            replace(record) if isinstance(record, LocalRecord) else record
            for record in self._records
        )

    @property
    def dependencies(self) -> tuple[Dependency, ...]:
        return self._dependencies

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
        self._lockfile = self._package.create_lockfile(output)
        return self._lockfile

    def get(self, refs: tuple[Ref, ...]) -> tuple[InstalledRecord, ...]:
        """Fetch selected package files together without requesting SCM sources."""
        return self._get(refs, with_sources=False)

    def get_with_sources(self, refs: tuple[Ref, ...]) -> tuple[InstalledRecord, ...]:
        """Fetch selected published packages in one install and reuse this snapshot."""
        return self._get(refs, with_sources=True)

    @property
    def editables(self) -> tuple[InstalledRecord, ...]:
        """The workspace source overrides captured when this graph was resolved."""
        return tuple(self._editables[ref] for ref in sorted(self._editables))

    def _get(self, refs: tuple[Ref, ...], *, with_sources: bool) -> tuple[InstalledRecord, ...]:
        selected = tuple(dict.fromkeys(self._package.resolve_ref(ref) for ref in refs))
        remote = tuple(ref for ref in selected if ref not in self._editables)
        records = (
            self._package.get_with_sources(remote) if with_sources else self._package.get(remote)
        )
        fetched = {record.ref: record for record in records}
        fetched.update(self._editables)
        return tuple(fetched[ref] for ref in selected)

    @property
    def networkx(self) -> _RefGraph:
        """Expose the cached read-only topology; use .copy() for a working graph."""
        return self._networkx

    @cached_property
    def _networkx(self) -> _RefGraph:
        """Build topology once; use .copy() when a mutable working graph is needed."""
        graph: nx.DiGraph[Ref] = nx.DiGraph()
        graph.add_nodes_from(record.ref for record in self._records)
        graph.add_edges_from((edge.source, edge.target) for edge in self._dependencies)
        nx.freeze(graph)
        return graph

    def ordered_refs(self, root: Ref, *, stop_at: tuple[Ref, ...] = ()) -> tuple[Ref, ...]:
        """Order reachable dependencies first, treating stop_at references as leaves."""
        try:
            root = self._package.resolve_ref(root)
            stops = tuple(self._package.resolve_ref(ref) for ref in stop_at)
        except InvalidPackage as error:
            raise ValueError(str(error)) from error
        key = (root, tuple(sorted(set(stops))))
        if key not in self._orders:
            self._orders[key] = self._order(root, key[1])
        return self._orders[key]

    def ordered_records(
        self, root: Ref, *, stop_at: tuple[Ref, ...] = ()
    ) -> tuple[LocalRecord | Record, ...]:
        """Return records in the same dependency-first order as ordered_refs."""
        records: list[LocalRecord | Record] = []
        for ref in self.ordered_refs(root, stop_at=stop_at):
            record = self._records_by_ref[ref]
            records.append(replace(record) if isinstance(record, LocalRecord) else record)
        return tuple(records)

    @cached_property
    def _records_by_ref(self) -> dict[Ref, LocalRecord | Record]:
        return {record.ref: record for record in self._records}

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
