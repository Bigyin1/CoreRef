"""Validation and projection of a native Conan graph, without package I/O."""

from collections.abc import Mapping

from ...api.errors import InvalidPackage
from ...api.graph import Dependency, GraphResult
from ...api.records import LocalRecord, Record, Ref
from .types import NativeGraph, NativePackage


class ResolvedConanGraph:
    """A native resolution with checked host dependencies and exact references."""

    def __init__(self, graph: NativeGraph) -> None:
        self._graph = graph
        self._packages = tuple(graph.root.conanfile.dependencies.host.topological_sort.values())
        for package in self._packages:
            self._validate_package(package)

    @property
    def references(self) -> tuple[str, ...]:
        return tuple(package.ref.repr_notime() for package in self._packages)

    def package(self, ref: Ref) -> NativePackage:
        return self._graph.root.conanfile.dependencies.host[str(ref)]

    def contains(self, ref: Ref) -> bool:
        return any(reference.split("#", 1)[0] == str(ref) for reference in self.references)

    def result(self, root: LocalRecord | Record, dependencies: tuple[Record, ...]) -> GraphResult:
        package = self._graph.root.conanfile
        if self._logical(package) != str(root.ref):
            raise InvalidPackage("Conan graph root differs from the requested reference")
        self._validate_package(package)
        records = {str(record.ref): record for record in (root, *dependencies)}
        return GraphResult(
            tuple(sorted(records.values(), key=lambda record: record.ref)),
            self._edges(records),
        )

    def _edges(self, records: Mapping[str, LocalRecord | Record]) -> tuple[Dependency, ...]:
        edges: set[Dependency] = set()
        for package in (self._graph.root.conanfile, *self._packages):
            source = self._logical(package)
            for target in package.dependencies.direct_host.values():
                destination = self._logical(target)
                if source not in records or destination not in records:
                    raise InvalidPackage("Conan graph contains an unknown endpoint")
                edges.add(Dependency(records[source].ref, records[destination].ref))
        return tuple(sorted(edges, key=lambda edge: (edge.source, edge.target)))

    @staticmethod
    def _logical(package: NativePackage) -> str:
        return package.ref.repr_notime().split("#", 1)[0]

    @staticmethod
    def _validate_package(package: NativePackage) -> None:
        if package.context != "host" or package.settings.dumps() or package.options.dumps():
            raise InvalidPackage("Configurable recipes and build dependencies are unsupported")
        if tuple(package.dependencies.build.values()):
            raise InvalidPackage("Build requirements are unsupported")
