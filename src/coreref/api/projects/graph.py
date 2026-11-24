"""Inspect locked source dependencies and open only the selected trees."""

from __future__ import annotations

from collections.abc import Collection
from graphlib import CycleError, TopologicalSorter
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from ..errors import Conflict, InvalidPackage, NotFound
from ..packages.catalog import Catalog, _Source
from ..packages.records import Dependency, LocalRecord, Record, Ref, _parse_dependency

if TYPE_CHECKING:
    from .manifest import Manifest


class _Lock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root: str
    requirements: dict[str, str]
    packages: dict[str, _Source]

    def resolve(self, dependency: Dependency) -> Ref:
        try:
            return Ref(self.requirements[str(dependency)])
        except KeyError as error:
            raise InvalidPackage(f"{dependency} is absent from manifest.lock. Run lock.") from error


def lock_sources(
    catalog: Catalog, manifest: Manifest, path: Path, additional: tuple[Dependency, ...]
) -> Path:
    """Resolve the manifest's source requests and save their exact Nix paths."""
    from .manifest import Manifest

    lock = _Lock(root=manifest.ref, requirements={}, packages={})
    pending = [*manifest.requirements, *additional]
    pending.extend(value.requirement for value in manifest.configured_dependencies.values())
    while pending:
        dependency = pending.pop()
        if str(dependency) in lock.requirements:
            continue
        ref, source = catalog._lock_entry(dependency)
        lock.requirements[str(dependency)] = str(ref)
        if str(ref) in lock.packages:
            continue
        lock.packages[str(ref)] = source
        pending += [_parse_dependency(item) for item in source.dependencies]
        if source.outputs:
            continue
        child = Manifest.load(Path(source.source) / "manifest.yml")
        pending.extend(value.requirement for value in child.configured_dependencies.values())
    lock = _Lock(
        root=lock.root,
        requirements=dict(sorted(lock.requirements.items())),
        packages=dict(sorted(lock.packages.items())),
    )
    _topological(_edges(lock, Ref(manifest.ref), manifest.requirements), Ref(manifest.ref))
    path.write_text(lock.model_dump_json(indent=2, exclude_defaults=True) + "\n", encoding="utf-8")
    return path


def _edges(
    lock: _Lock, root: Ref, requirements: tuple[Dependency, ...]
) -> dict[Ref, tuple[Ref, ...]]:
    edges = {
        Ref(ref): tuple(lock.resolve(_parse_dependency(item)) for item in source.dependencies)
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
        record: LocalRecord,
        *,
        dependencies: tuple[Dependency, ...] = (),
        lockfile: Path,
    ) -> None:
        """Bind the root's requirements to the supplied lockfile."""
        self.catalog = catalog
        self.record = record
        self._lock = _Lock.model_validate_json(lockfile.read_bytes())
        self._entries = {Ref(ref): source for ref, source in self._lock.packages.items()}
        self._records = {ref: source.record(ref) for ref, source in self._entries.items()}
        self._edges = _edges(self._lock, record.ref, dependencies)

    @property
    def records(self) -> tuple[LocalRecord | Record, ...]:
        """Return the working root and all locked dependency metadata."""
        return (self.record, *self._records.values())

    def _ref(self, ref: Ref) -> Ref:
        if ref in self._edges:
            return ref
        if ref.revision is None:
            matches = [exact for exact in self._entries if exact.without_revision() == ref]
            if len(matches) == 1:
                return matches[0]
            if matches:
                raise Conflict(f"Specify the exact locked revision of {ref}")
        raise NotFound(f"{ref} is absent from manifest.lock")

    def info(self, ref: Ref) -> LocalRecord | Record:
        """Return metadata for a locked node."""
        exact = self._ref(ref)
        return self.record if exact == self.record.ref else self._records[exact]

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
        root = self.record.ref if ref is None else self._ref(ref)
        stops = {self._ref(boundary) for boundary in stop_at}
        if root in stops:
            return ()
        refs = _topological(self._edges, root, stops)[:-1] if transitive else self._edges[root]
        return tuple(self._records[item] for item in refs)

    def get_sources(self, refs: tuple[Ref, ...]) -> dict[Ref, Path]:
        """Retrieve the selected source trees and keep the requested keys."""
        return {ref: self._source(ref) for ref in refs}

    def _source(self, ref: Ref) -> Path:
        exact = self._ref(ref)
        if exact == self.record.ref and self.record.source is not None:
            return self.record.source
        return self.catalog._source_for(exact, self._entries[exact])

    def _resolve(self, dependency: Dependency) -> Record:
        return self._records[self._lock.resolve(dependency)]
