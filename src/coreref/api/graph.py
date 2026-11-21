"""Resolved dependencies, a working local root, ordering and lockfiles."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from .errors import CatalogError, InvalidPackage
from .records import Dependency, InstalledRecord, LocalRecord, Record, Ref

if TYPE_CHECKING:
    from .._internal.package import LocalPackage
    from .catalog import Catalog
    from .query import SearchQuery


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
        dependencies: tuple[Dependency, ...] = (),
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
        self._orders: dict[Ref, tuple[Ref, ...]] = {}

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
        records = []
        targets = self.ordered_refs(selected)[:-1] if transitive else self._neighbors[selected]
        for target in targets:
            record = self._index[target]
            if isinstance(record, Record) and (filters is None or filters.matches(record)):
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

    def get_sources(self, refs: tuple[Ref, ...]) -> dict[Ref, Path]:
        """Fetch Git sources only, keyed by exact refs. Omit packages without sources."""
        return self._package.get_sources(refs)

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
    def edges(self) -> tuple[tuple[Ref, Ref], ...]:
        """Read direct edges selected by Conan, from consumer to dependency."""
        _ = self.record
        return self._dependencies

    def ordered_refs(self, root: Ref) -> tuple[Ref, ...]:
        """Return reachable packages in Conan order, including the root."""
        _ = self.record
        root = self._package.resolve_ref(root)
        if root not in self._orders:
            references = self._package.graph.ordered_dependencies(root)
            self._orders[root] = (*map(Ref, references), root)
        return self._orders[root]

    def ordered_records(self, root: Ref) -> tuple[LocalRecord | Record, ...]:
        """Return records in the same dependency-first order as ordered_refs."""
        return tuple(self.info(ref) for ref in self.ordered_refs(root))


__all__ = ["Graph"]
