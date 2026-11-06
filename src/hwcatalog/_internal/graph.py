"""Validation and projection of a native Conan graph, without package I/O."""

from collections.abc import Mapping
from functools import cached_property

from ..api.errors import InvalidPackage
from ..api.graph import Dependency
from ..api.records import LocalRecord, Record, Ref
from .types import NativeGraph, NativePackage

type GraphData = tuple[tuple[LocalRecord | Record, ...], tuple[Dependency, ...]]


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

    def package(self, ref: Ref) -> NativePackage:
        return self.native.root.conanfile.dependencies.host[str(ref)]

    def contains(self, ref: Ref) -> bool:
        return any(reference.split("#", 1)[0] == str(ref) for reference in self.references)

    def result(self, root: LocalRecord | Record, dependencies: tuple[Record, ...]) -> GraphData:
        package = self.native.root.conanfile
        if str(package.ref) != str(root.ref):
            raise InvalidPackage("Conan graph root differs from the requested reference")
        self._validate_package(package)
        records = {str(record.ref): record for record in (root, *dependencies)}
        return (
            tuple(sorted(records.values(), key=lambda record: record.ref)),
            self._edges(records),
        )

    def _edges(self, records: Mapping[str, LocalRecord | Record]) -> tuple[Dependency, ...]:
        edges: set[Dependency] = set()
        for package in (self.native.root.conanfile, *self._packages):
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
