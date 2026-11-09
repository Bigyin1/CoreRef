"""Validation and projection of a native Conan graph, without package I/O."""

from __future__ import annotations

from collections.abc import Mapping
from functools import cached_property
from pathlib import Path

from ..api.errors import InvalidPackage
from ..api.graph import Dependency
from ..api.records import LocalRecord, Record, Ref
from .types import NativeGraph, NativePackage

GraphData = tuple[tuple[LocalRecord | Record, ...], tuple[Dependency, ...]]


class ResolvedConanGraph:
    """A native resolution with checked host dependencies and exact references."""

    def __init__(self, graph: NativeGraph) -> None:
        self.native = graph
        self._packages = tuple(graph.root.conanfile.dependencies.host.topological_sort.values())
        for package in self._packages:
            self._validate_package(package)

    @cached_property
    def references(self) -> tuple[str, ...]:
        return tuple(package.ref.repr_notime() for package in self._packages)

    @property
    def direct_references(self) -> tuple[str, ...]:
        return tuple(
            package.ref.repr_notime()
            for package in self.native.root.conanfile.dependencies.direct_host.values()
        )

    def package_ref(self, ref: Ref) -> Ref:
        return Ref(self.package(ref).ref.repr_notime(), catalog=ref._catalog)

    def package(self, ref: Ref) -> NativePackage:
        package = self.native.root.conanfile.dependencies.host[str(ref.without_revision())]
        if ref.revision is not None and package.ref.repr_notime() != str(ref):
            raise InvalidPackage(f"Conan selected a different revision for {ref}")
        return package

    @cached_property
    def editable_references(self) -> frozenset[str]:
        return frozenset(
            node.conanfile.ref.repr_notime()
            for node in self.native.nodes
            if node.recipe == "Editable"
        )

    def recipe_directory(self, ref: Ref) -> Path:
        """Return the recipe folder already loaded for this exact graph node."""
        directory = self.package(ref).recipe_folder
        if directory is None:
            raise InvalidPackage(f"Conan did not provide the recipe directory for {ref}")
        return Path(directory)

    def contains(self, ref: Ref) -> bool:
        if ref.revision is not None:
            return str(ref) in self.references
        return any(reference.split("#", 1)[0] == str(ref) for reference in self.references)

    def result(self, root: LocalRecord | Record, dependencies: tuple[Record, ...]) -> GraphData:
        package = self.native.root.conanfile
        if self.native.root.ref is None or self.native.root.ref.name is None:
            package = self.package(root.ref)
        elif str(package.ref) != str(root.ref.without_revision()):
            raise InvalidPackage("Conan graph root differs from the requested reference")
        self._validate_package(package)
        records = {str(record.ref.without_revision()): record for record in (root, *dependencies)}
        return (
            tuple(sorted(records.values(), key=lambda record: record.ref)),
            self._edges(records),
        )

    def _edges(self, records: Mapping[str, LocalRecord | Record]) -> tuple[Dependency, ...]:
        edges: set[Dependency] = set()
        packages = self._packages
        if self.native.root.ref is not None and self.native.root.ref.name is not None:
            packages = (self.native.root.conanfile, *packages)
        for package in packages:
            source = str(package.ref)
            for target in package.dependencies.direct_host.values():
                destination = str(target.ref)
                if source not in records or destination not in records:
                    raise InvalidPackage("Conan graph contains an unknown endpoint")
                edges.add(Dependency(records[source].ref, records[destination].ref))
        return tuple(sorted(edges, key=lambda edge: (edge.source, edge.target)))

    @staticmethod
    def _validate_package(package: NativePackage) -> None:
        if package.context != "host" or package.settings.dumps() or package.options.dumps():
            raise InvalidPackage("Configurable recipes and build dependencies are unsupported")
        if tuple(package.dependencies.build.values()):
            raise InvalidPackage("Build requirements are unsupported")
