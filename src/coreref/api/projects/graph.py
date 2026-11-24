"""Inspect locked source dependencies and open only the selected trees."""

from __future__ import annotations

from collections.abc import Collection
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ..errors import InvalidPackage, NotFound
from ..packages.catalog import Catalog, _Source
from ..packages.records import Record, Ref

if TYPE_CHECKING:
    from .manifest import Manifest


class _Lock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root: str
    requirements: dict[str, str]
    packages: dict[str, _Source]

    def resolve(self, dependency: str) -> Ref:
        try:
            selected = self.requirements[dependency]
        except KeyError as error:
            raise InvalidPackage(f"{dependency} is absent from manifest.lock. Run lock.") from error
        if selected not in self.packages:
            raise InvalidPackage(f"{dependency} selects {selected}, absent from manifest.lock")
        return Ref(selected)


def lock_sources(
    catalog: Catalog, manifest: Manifest, path: Path, additional: tuple[str, ...]
) -> Path:
    """Resolve the manifest's source requests and save their exact Nix paths."""
    lock = _Lock(root=manifest.ref, requirements={}, packages={})
    pending = [*manifest.requirements, *additional]
    pending.extend(value.ref for value in manifest.configured_dependencies.values())
    while pending:
        dependency = pending.pop()
        if dependency in lock.requirements:
            continue
        ref = catalog._select(dependency)
        key = str(ref)
        lock.requirements[dependency] = key
        if key in lock.packages:
            continue
        source, configured = catalog._lock_entry(ref)
        lock.packages[key] = source
        pending.extend(source.dependencies)
        pending.extend(configured)
    lock = _Lock(
        root=lock.root,
        requirements=dict(sorted(lock.requirements.items())),
        packages=dict(sorted(lock.packages.items())),
    )
    _topological(_edges(lock, Ref(manifest.ref), manifest.requirements), Ref(manifest.ref))
    path.write_text(lock.model_dump_json(indent=2, exclude_defaults=True) + "\n", encoding="utf-8")
    return path


def _edges(lock: _Lock, root: Ref, requirements: tuple[str, ...]) -> dict[Ref, tuple[Ref, ...]]:
    edges = {
        Ref(ref): tuple(lock.resolve(item) for item in source.dependencies)
        for ref, source in lock.packages.items()
    }
    edges[root] = tuple(lock.resolve(item) for item in requirements)
    return edges


def _topological(
    edges: dict[Ref, tuple[Ref, ...]], root: Ref, stops: Collection[Ref] = ()
) -> tuple[Ref, ...]:
    reachable: dict[Ref, tuple[Ref, ...]] = {}
    pending = [root]
    while pending:
        ref = pending.pop()
        if ref in reachable:
            continue
        neighbors = () if ref in stops else edges[ref]
        reachable[ref] = neighbors
        pending.extend(neighbors)
    try:
        return tuple(TopologicalSorter(reachable).static_order())
    except CycleError as error:
        cycle = " -> ".join(map(str, error.args[1]))
        raise InvalidPackage(f"Dependency cycle: {cycle}") from error


class Graph:
    """Keep dependency metadata from a project lockfile.

    Topological queries read the snapshot in memory. Source trees are
    retrieved when requested, and an editable checkout may replace a
    locked tree if explicitly configured in the catalog.
    """

    def __init__(
        self,
        catalog: Catalog,
        manifest: Manifest,
        source: Path,
        *,
        lockfile: Path,
    ) -> None:
        """Bind the root's requirements to the supplied lockfile."""
        self.catalog = catalog
        self._lock = _Lock.model_validate_json(lockfile.read_bytes())
        self._root = Ref(manifest.ref)
        self._records: dict[Ref, Record] = {}
        for text, entry in self._lock.packages.items():
            ref = Ref(text)
            self._records[ref] = entry.record(ref)
        root = _Source(source=source, kind=manifest.type, payload=manifest.payload)
        self._records[self._root] = root.record(self._root)
        self._edges = _edges(self._lock, self._root, manifest.requirements)

    def _ref(self, ref: Ref) -> Ref:
        if ref in self._edges:
            return ref
        raise NotFound(f"{ref} is absent from manifest.lock")

    def info(self, ref: Ref) -> Record:
        """Return metadata for a locked node."""
        return self._records[self._ref(ref)]

    def dependencies(
        self,
        ref: Ref | None = None,
        *,
        transitive: bool = False,
        stop_at: Collection[Ref] = (),
    ) -> tuple[Record, ...]:
        """Return direct dependencies, or traverse them in build order.

        Boundaries in ``stop_at`` remain visible. A boundary's descendants
        are kept whenever another selected path reaches them.
        """
        root = self._root if ref is None else self._ref(ref)
        stops = {self._ref(boundary) for boundary in stop_at}
        if root in stops:
            return ()
        refs = _topological(self._edges, root, stops)[:-1] if transitive else self._edges[root]
        return tuple(self._records[item] for item in refs)

    def get_sources(self, refs: tuple[Ref, ...]) -> dict[Ref, Path]:
        """Retrieve the selected source trees and keep the requested keys."""
        return {ref: self._source(ref) for ref in refs}

    def _source(self, ref: Ref) -> Path:
        self._ref(ref)
        if ref == self._root:
            return self._records[ref].source
        return self.catalog._source_for(ref, self._lock.packages[str(ref)])

    def _resolve(self, dependency: str) -> Record:
        return self._records[self._lock.resolve(dependency)]
