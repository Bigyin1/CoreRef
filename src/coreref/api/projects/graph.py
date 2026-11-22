"""Resolve Conan dependencies and keep their selection for later operations.

A ``Graph`` can inspect stored metadata, fetch selected files and sources
in batches, or write the already-resolved graph to a project lockfile.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from ..errors import CatalogError, InvalidPackage
from ..packages.records import Dependency, InstalledRecord, LocalRecord, Record, Ref

if TYPE_CHECKING:
    from ..._internal.package import LocalPackage
    from ..packages.catalog import Catalog
    from ..packages.query import SearchQuery


@dataclass(init=False)
class Graph:
    """Keep a dependency resolution for a published package or local root.

    Conan resolves dependencies and reads recipe metadata when the graph
    is constructed. Inspection methods then use the snapshot in memory.
    Fetching follows the selected revisions, including for references
    that omit their revision. The local root stays available as the same
    mutable ``LocalRecord`` supplied by the caller.
    """

    _records: tuple[LocalRecord | Record, ...]
    _dependencies: tuple[tuple[Ref, Ref], ...]

    def __init__(
        self,
        catalog: Catalog,
        record: LocalRecord | Ref,
        *,
        dependencies: tuple[Dependency, ...] = (),
        lockfile: Path | None = None,
    ) -> None:
        """Resolve a package root using the supplied catalog.

        Pass a published ``Ref`` to read its recipe requirements or a
        ``LocalRecord`` to supply ``dependencies`` explicitly. An existing
        ``lockfile`` constrains resolution. A missing path is remembered as
        the default destination for ``create_lockfile()``.
        """
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
        self._orders: dict[Ref, tuple[Ref, ...]] = {}

    @property
    def catalog(self) -> Catalog:
        """Return the catalog used for resolution and subsequent retrieval."""
        return self._catalog

    @property
    def record(self) -> LocalRecord | Record:
        """Return the working local root or the published root's metadata.

        Create a new graph if the coordinates of a local root change.
        """
        record = self._package.record
        if record.ref != self._root_ref:
            raise InvalidPackage("Local root coordinates changed; create a new Graph")
        return record

    @property
    def records(self) -> tuple[LocalRecord | Record, ...]:
        """Return metadata for every resolved node, including the root.

        A local root is the same working record returned by ``record``.
        """
        _ = self.record
        return self._records

    def info(self, ref: Ref) -> LocalRecord | Record:
        """Look up a node in the resolved snapshot without package I/O.

        An unpinned reference matches the revision already selected by this
        graph. A supplied revision must agree with that selection.
        """
        ref = self._package.resolve_ref(ref)
        _ = self.record
        return self._index[ref]

    def dependencies(
        self,
        ref: Ref | None = None,
        *,
        transitive: bool = False,
        filters: SearchQuery | None = None,
        stop_at: Collection[Ref] = (),
    ) -> tuple[Record, ...]:
        """Return dependency metadata without performing package I/O.

        Omit ``ref`` to start at the root. The default returns direct
        dependencies. Set ``transitive=True`` to visit each reachable package
        once, dependencies before consumers. ``filters`` selects which
        records to return after traversal. ``stop_at`` keeps boundary packages
        visible while pruning their descendants along that path. Shared
        dependencies remain visible when reached by another path.
        """
        selected = self.record.ref if ref is None else self._package.resolve_ref(ref)
        stops = {self._package.resolve_ref(boundary) for boundary in stop_at}
        if selected in stops:
            return ()
        targets = self._ordered_refs(selected)[:-1] if transitive else self._neighbors[selected]
        if transitive and stops:
            reachable = self._reachable(selected, stops)
            targets = tuple(target for target in targets if target in reachable)
        return self._matching_records(targets, filters)

    def _matching_records(
        self, targets: tuple[Ref, ...], filters: SearchQuery | None
    ) -> tuple[Record, ...]:
        records = []
        for target in targets:
            record = self._index[target]
            if isinstance(record, Record) and (filters is None or filters.matches(record)):
                records.append(record)
        return tuple(records)

    def _reachable(self, root: Ref, stops: set[Ref]) -> set[Ref]:
        pending = [root]
        visited: set[Ref] = set()
        while pending:
            ref = pending.pop()
            if ref in visited:
                continue
            visited.add(ref)
            if ref not in stops:
                pending.extend(self._neighbors[ref])
        return visited

    @cached_property
    def _neighbors(self) -> dict[Ref, tuple[Ref, ...]]:
        neighbors: dict[Ref, list[Ref]] = {record.ref: [] for record in self._records}
        for source, target in self._dependencies:
            neighbors[source].append(target)
        return {ref: tuple(targets) for ref, targets in neighbors.items()}

    def _pack(self, record: LocalRecord | None = None) -> InstalledRecord:
        if self._local is None:
            raise InvalidPackage(
                "Only a local graph can be packed; use a LocalRecord for a new package"
            )
        root = self.record
        if record is not None and record.ref != root.ref:
            raise InvalidPackage("Publication coordinates differ from the resolved root")
        return self._local.pack(resolved=self._package.graph, record=record)

    @property
    def lockfile(self) -> Path | None:
        """Return the configured or last successfully saved lockfile path."""
        return self._lockfile

    def create_lockfile(self, path: str | Path | None = None) -> Path:
        """Write the current resolution to a Conan lockfile.

        Use the supplied path or the graph's remembered path. If neither is
        available, raise ``CatalogError``. The chosen destination becomes
        ``lockfile`` after a successful write.
        """
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
        with_sources: bool = False,
    ) -> tuple[InstalledRecord, ...]:
        """Get files for selected nodes, optionally with their Git sources.

        The default fetches every resolved package in dependency order,
        excluding an unpublished local root. An explicit tuple limits the
        result to those nodes, in request order with duplicates removed.
        Pass an empty tuple for no results. Fetching uses this graph's
        resolution and batches packages that need installation.
        """
        if refs is None:
            refs = self._ordered_refs(self._root_ref)
            if self._local is not None:
                refs = refs[:-1]
        return self._package.get(refs, with_sources=with_sources)

    def get_sources(self, refs: tuple[Ref, ...]) -> dict[Ref, Path]:
        """Fetch sources for the selected packages and return paths by resolved Ref.

        Packages without declared Git sources are omitted. Use this operation
        when you only need sources, such as when generating an RTL file list.
        Editable paths can point into a working checkout.
        """
        return self._package.get_sources(refs)

    @property
    def edges(self) -> tuple[tuple[Ref, Ref], ...]:
        """Return direct edges as pairs from consumer to dependency."""
        _ = self.record
        return self._dependencies

    def _ordered_refs(self, root: Ref) -> tuple[Ref, ...]:
        """Return reachable packages in Conan order, including the root."""
        _ = self.record
        root = self._package.resolve_ref(root)
        if root not in self._orders:
            references = self._package.graph.ordered_dependencies(root)
            self._orders[root] = (*map(Ref, references), root)
        return self._orders[root]


__all__ = ["Graph"]
