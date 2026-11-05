"""Resolve and validate native Conan dependency graphs."""

from pathlib import Path
from tempfile import TemporaryDirectory

from ...api.errors import InvalidPackage
from ...api.models.graph import Dependency, GraphResult
from ...api.models.query import SearchQuery
from ...api.models.records import Record, Ref
from ..validation import normalize_publication, normalize_query, normalize_ref
from . import records
from .client import ConanClient
from .format import (
    from_conan_reference,
    read_recipe,
    write_recipe,
)
from .types import NativeGraph, NativePackage


def resolve_local(
    client: ConanClient,
    record: Record,
    *,
    dependencies: tuple[Ref, ...] = (),
    lockfile: Path | None = None,
) -> GraphResult:
    """Resolve local root data independently of its publication status."""
    record, dependencies = normalize_publication(record, dependencies)
    requirements = tuple(records.requirement(client, ref) for ref in dependencies)
    with TemporaryDirectory(prefix="hwcatalog-conan-resolve-") as temporary:
        directory = Path(temporary)
        write_recipe(directory, record, requirements)
        graph = client.graph(directory, lockfile=lockfile)
        record_map = graph_records(client, graph, record)
        return GraphResult(
            tuple(sorted(record_map.values(), key=lambda item: item.ref)),
            _graph_dependencies(graph, record_map),
        )


def find_dependents(
    client: ConanClient, ref: Ref, *, candidates: SearchQuery, transitive: bool = True
) -> tuple[Record, ...]:
    ref = normalize_ref(ref)
    records.get(client, ref)
    result = []
    for candidate in records.search(client, normalize_query(candidates)):
        if candidate.ref == ref:
            continue
        if transitive:
            graph, _ = load_graph(client, candidate.ref)
            matches = any(
                _package_ref(package) == ref
                for package in graph.root.conanfile.dependencies.host.values()
            )
        else:
            matches = any(
                record.ref == ref for record in records.dependencies(client, candidate.ref)
            )
        if matches:
            result.append(candidate)
    return tuple(sorted(result, key=lambda record: record.ref))


def load_graph(
    client: ConanClient, ref: Ref, *, lockfile: Path | None = None
) -> tuple[NativeGraph, dict[Ref, Record]]:
    _, directory = records.locate(client, ref)
    root, _ = read_recipe(directory, ref)
    graph = client.graph(directory, lockfile=lockfile)
    return graph, graph_records(client, graph, root)


def graph_records(client: ConanClient, graph: NativeGraph, root: Record) -> dict[Ref, Record]:
    root_package = graph.root.conanfile
    if _package_ref(root_package) != root.ref:
        raise InvalidPackage("Conan graph root differs from the requested reference")
    _validate_package(root_package)
    return {root.ref: root, **dependency_records(client, graph)}


def dependency_records(client: ConanClient, graph: NativeGraph) -> dict[Ref, Record]:
    packages = tuple(graph.root.conanfile.dependencies.host.topological_sort.values())
    for package in packages:
        _validate_package(package)
    return records.read_records(client, tuple(package.ref.repr_notime() for package in packages))


def _package_ref(package: NativePackage) -> Ref:
    return from_conan_reference(package.ref.repr_notime())


def _validate_package(package: NativePackage) -> None:
    if package.context != "host" or package.settings.dumps() or package.options.dumps():
        raise InvalidPackage("Configurable recipes and build dependencies are unsupported")
    if tuple(package.dependencies.build.values()):
        raise InvalidPackage("Build requirements are unsupported")


def _graph_dependencies(graph: NativeGraph, records: dict[Ref, Record]) -> tuple[Dependency, ...]:
    root = graph.root.conanfile
    packages = (root, *root.dependencies.host.values())
    edges: set[Dependency] = set()
    for package in packages:
        source = _package_ref(package)
        for target in package.dependencies.direct_host.values():
            target_ref = _package_ref(target)
            if source not in records or target_ref not in records:
                raise InvalidPackage("Conan graph contains an unknown endpoint")
            edges.add(Dependency(source, target_ref))
    return tuple(sorted(edges, key=lambda edge: (edge.source, edge.target)))
