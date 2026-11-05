"""Conan package storage with project-owned lockfiles and nontransactional publication."""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from ...api.errors import (
    BackendUnavailable,
    IdentityConflict,
    InvalidPackage,
    InvalidPublication,
    MaterializationError,
    NotFound,
)
from ...api.graph import CatalogGraph
from ...api.models.graph import Dependency, GraphResult
from ...api.models.query import SearchQuery
from ...api.models.records import Record, Ref
from ...api.models.resources import MaterializedPackage
from ..manifest import Manifest, read_manifest
from ..validation import (
    normalize_publication,
    normalize_query,
    normalize_ref,
)
from .conan_client import ConanClient
from .conan_format import (
    from_conan_reference,
    read_recipe,
    search_pattern,
    to_conan_reference,
    write_recipe,
)
from .conan_types import NativeGraph, NativePackage


class ConanGraph:
    def __init__(self, backend: ConanBackend) -> None:
        self.backend = backend

    def dependencies(self, ref: Ref, filters: SearchQuery = SearchQuery()) -> tuple[Record, ...]:
        ref, filters = normalize_ref(ref), normalize_query(filters)
        with self.backend._operation():
            requirements = self.backend._requirements(ref)
            records = self.backend._read_records(requirements).values()
            return tuple(sorted((r for r in records if filters.matches(r)), key=lambda r: r.ref))

    def consumers(self, ref: Ref, filters: SearchQuery = SearchQuery()) -> tuple[Record, ...]:
        return self.find_dependents(ref, candidates=filters, transitive=False)

    def resolve_local(
        self,
        record: Record,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> GraphResult:
        """Resolve local root data independently of its publication status."""
        record, dependencies = normalize_publication(record, dependencies)
        with self.backend._operation():
            requirements = tuple(self.backend._requirement(ref) for ref in dependencies)
            with TemporaryDirectory(prefix="hwcatalog-conan-resolve-") as temporary:
                directory = Path(temporary)
                write_recipe(directory, record, requirements)
                graph = self.backend._client.graph(directory, lockfile=lockfile)
                records = self.backend._graph_records(graph, record)
                return GraphResult(
                    tuple(sorted(records.values(), key=lambda item: item.ref)),
                    _graph_dependencies(graph, records),
                )

    def find_dependents(
        self, ref: Ref, *, candidates: SearchQuery, transitive: bool = True
    ) -> tuple[Record, ...]:
        ref = normalize_ref(ref)
        with self.backend._operation():
            self.backend.get_many((ref,))
            result = []
            for candidate in self.backend.search(normalize_query(candidates)):
                if candidate.ref == ref:
                    continue
                if transitive:
                    graph, _ = self.backend._load_graph(candidate.ref)
                    matches = any(
                        _package_ref(package) == ref
                        for package in graph.root.conanfile.dependencies.host.values()
                    )
                else:
                    matches = any(record.ref == ref for record in self.dependencies(candidate.ref))
                if matches:
                    result.append(candidate)
            return tuple(sorted(result, key=lambda record: record.ref))


class ConanBackend:
    """One local cache or one configured remote; callers coordinate other publishers.

    Only internally generated recipes are accepted. Package contents use Conan
    binaries without settings/options; lockfiles belong to consuming projects.
    """

    def __init__(
        self,
        home: Path | None = None,
        *,
        remote: str | None = None,
        timeout: float = 60,
    ) -> None:
        self._client = ConanClient(home, remote, timeout)
        self._lock = threading.RLock()
        self._closed = False
        self.graph: CatalogGraph = ConanGraph(self)

    @contextmanager
    def _operation(self) -> Iterator[None]:
        with self._lock:
            if self._closed:
                raise BackendUnavailable("Conan backend is closed")
            yield

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def _locate(self, ref: Ref) -> tuple[str, Path]:
        logical = to_conan_reference(ref)
        revisions = self._client.index(f"{logical}*#*").get(logical)
        if revisions is None or not revisions.revisions:
            raise NotFound(ref)
        if len(revisions.revisions) != 1:
            raise IdentityConflict(f"Multiple recipe revisions for {logical}")
        pinned = f"{logical}#{next(iter(revisions.revisions))}"
        return pinned, self._client.cache_path(pinned)

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        with self._operation():
            refs = tuple(normalize_ref(ref) for ref in refs)
            records = self._read_records(tuple(to_conan_reference(ref) for ref in refs))
            return tuple(records[ref] for ref in refs)

    def _read_records(self, references: tuple[str, ...]) -> dict[Ref, Record]:
        records = {}
        for reference in dict.fromkeys(references):
            ref = from_conan_reference(reference)
            published, directory = self._locate(ref)
            if "#" in reference and published != reference:
                raise IdentityConflict(
                    f"Locked revision differs from published identity: {reference}"
                )
            records[ref] = read_recipe(directory, ref)[0]
        return records

    def _requirements(self, ref: Ref) -> tuple[str, ...]:
        _, directory = self._locate(ref)
        return read_recipe(directory, ref)[1].requires

    def search(self, query: SearchQuery) -> tuple[Record, ...]:
        query = normalize_query(query)
        with self._operation():
            pattern = search_pattern(query)
            if pattern is None:
                return ()
            candidates = self._client.index(pattern)
            refs = tuple(from_conan_reference(value) for value in candidates)
            records = self.get_many(refs)
            return tuple(sorted((r for r in records if query.matches(r)), key=lambda r: r.ref))

    def _load_graph(
        self, ref: Ref, *, lockfile: Path | None = None
    ) -> tuple[NativeGraph, dict[Ref, Record]]:
        _, directory = self._locate(ref)
        root, _ = read_recipe(directory, ref)
        graph = self._client.graph(directory, lockfile=lockfile)
        return graph, self._graph_records(graph, root)

    def _graph_records(self, graph: NativeGraph, root: Record) -> dict[Ref, Record]:
        root_package = graph.root.conanfile
        if _package_ref(root_package) != root.ref:
            raise InvalidPackage("Conan graph root differs from the requested reference")
        _validate_package(root_package)
        return {root.ref: root, **self._dependency_records(graph)}

    def _dependency_records(self, graph: NativeGraph) -> dict[Ref, Record]:
        packages = tuple(graph.root.conanfile.dependencies.host.topological_sort.values())
        for package in packages:
            _validate_package(package)
        return self._read_records(tuple(package.ref.repr_notime() for package in packages))

    def publish(
        self,
        record: Record,
        *,
        dependencies: tuple[Ref, ...] = (),
        lockfile: Path | None = None,
    ) -> Record:
        return self._publish(record, dependencies, lockfile=lockfile)

    def publish_file(self, path: Path, *, lockfile: Path | None = None) -> Record:
        manifest = read_manifest(path)
        return self._publish(
            manifest.record, manifest.dependencies, manifest=manifest, lockfile=lockfile
        )

    def create_lockfile(self, refs: tuple[Ref, ...], path: Path) -> Path:
        """Write a Conan lockfile owned by an RTL repository or BE workspace."""
        output = path.expanduser().resolve()
        with self._operation():
            references = tuple(self._locate(normalize_ref(ref))[0] for ref in dict.fromkeys(refs))
            output.parent.mkdir(parents=True, exist_ok=True)
            self._client.create_project_lockfile(
                references, output, seed=output if output.is_file() else None
            )
        return output

    def _requirement(self, ref: Ref) -> str:
        pinned, directory = self._locate(ref)
        read_recipe(directory, ref)
        return pinned

    def _publish(
        self,
        record: Record,
        dependencies: tuple[Ref, ...],
        *,
        manifest: Manifest | None = None,
        lockfile: Path | None = None,
    ) -> Record:
        record, dependencies = normalize_publication(record, dependencies)
        if record.files and manifest is None:
            raise InvalidPublication("Publish stored files using a package JSON document")
        logical = to_conan_reference(record.ref)
        with self._operation():
            if logical in self._client.index(f"{logical}*#*"):
                raise IdentityConflict("Record identity already published")
            requirements = tuple(self._requirement(target) for target in dependencies)
            with TemporaryDirectory(prefix="hwcatalog-conan-publish-") as temporary:
                directory = Path(temporary)
                write_recipe(
                    directory,
                    record,
                    requirements,
                    source_path=manifest.source_path if manifest else None,
                    files=manifest.files if manifest else None,
                )
                graph = self._client.export_package(directory, lockfile=lockfile)
                reference = graph.root.ref
                if reference is None:
                    raise InvalidPackage("Conan did not return the exported recipe reference")
                pinned = reference.repr_notime()
                exported = self._client.cache_path(pinned, download=False)
                stored, _ = read_recipe(exported, record.ref)
                self._client.upload(pinned)
                return stored

    def materialize(
        self, ref: Ref, *, with_sources: bool = False, lockfile: Path | None = None
    ) -> MaterializedPackage:
        """Install artifacts and optionally run Conan source retrieval for this package."""
        with self._operation():
            pinned, directory = self._locate(normalize_ref(ref))
            record, _ = read_recipe(directory, ref)
            if not record.source and not record.files:
                raise MaterializationError("Package has no stored source or artifacts")
            with TemporaryDirectory(prefix="hwcatalog-conan-install-") as temporary:
                lock = None
                if lockfile is not None:
                    lock = Path(temporary) / "conan.lock"
                    self._client.extend_lockfile(pinned, lockfile, lock)
                result = self._client.install(
                    pinned, lock, Path(temporary), with_sources=with_sources
                )
                self._dependency_records(result)
                package = result.root.conanfile.dependencies.host[to_conan_reference(ref)]
                if package.package_folder is None:
                    raise MaterializationError("Conan did not install the data package")
                source_path = None
                if with_sources and record.source is not None:
                    if package.folders.source_folder is None:
                        raise MaterializationError("Conan did not provide the SCM source folder")
                    source_path = Path(package.folders.source_folder) / "source"
                return MaterializedPackage(Path(package.package_folder), source_path)


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
