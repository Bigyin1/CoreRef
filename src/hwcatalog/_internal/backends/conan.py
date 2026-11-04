"""Conan recipe storage with exact root lockfiles and nontransactional publication."""

import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import networkx as nx

from ...api.errors import (
    BackendUnavailable,
    IdentityConflict,
    InvalidPackage,
    InvalidPublication,
    MaterializationError,
    NotFound,
    PartialPublicationError,
    VersionConflict,
)
from ...api.graph import CatalogGraph
from ...api.models.graph import Dependency, GraphResult
from ...api.models.query import SearchQuery
from ...api.models.records import Record, Ref
from ...api.models.resources import MaterializedPackage
from ..manifest import Manifest, read_manifest
from ..validation import (
    normalize_dependency,
    normalize_query,
    normalize_record,
    normalize_ref,
    validate,
)
from .conan_client import ConanClient
from .conan_format import (
    Lockfile,
    from_conan_reference,
    lock_references,
    read_recipe,
    search_pattern,
    to_conan_reference,
    write_recipe,
)
from .conan_types import NativeGraph, NativePackage


@dataclass(frozen=True)
class PreparedRecipe:
    record_ref: Ref
    directory: Path
    reference: str


class ConanReader:
    def __init__(self, backend: ConanBackend) -> None:
        self.backend = backend

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        return self.backend.get_many(refs)

    def search(self, query: SearchQuery) -> tuple[Record, ...]:
        return self.backend.search(query)

    def dependencies(self, ref: Ref, filters: SearchQuery) -> tuple[Record, ...]:
        graph, records = self.backend._load_graph(ref)
        packages = graph.root.conanfile.dependencies.direct_host.values()
        matches = (records[_package_ref(package)] for package in packages)
        return tuple(
            sorted((r for r in matches if filters.matches(r)), key=lambda r: r.ref.identity)
        )

    def consumers(self, ref: Ref, filters: SearchQuery) -> tuple[Record, ...]:
        return self.backend.graph.find_dependents(ref, candidates=filters, transitive=False)


class ConanGraph(CatalogGraph):
    def __init__(self, backend: ConanBackend) -> None:
        super().__init__(backend._read)
        self.backend = backend

    def dependency_closure(self, ref: Ref) -> GraphResult:
        """Use Conan's graph with the selected root's exported lockfile."""
        with self.backend._read():
            return self.backend._closure(normalize_ref(ref))

    def find_dependents(
        self, ref: Ref, *, candidates: SearchQuery, transitive: bool = True
    ) -> tuple[Record, ...]:
        ref = normalize_ref(ref)
        with self.backend._read() as reader:
            reader.get_many((ref,))
            result = []
            for candidate in reader.search(normalize_query(candidates)):
                if candidate.ref == ref:
                    continue
                graph, _ = self.backend._load_graph(candidate.ref)
                dependencies = graph.root.conanfile.dependencies
                packages = dependencies.host if transitive else dependencies.direct_host
                if any(_package_ref(package) == ref for package in packages.values()):
                    result.append(candidate)
            return tuple(sorted(result, key=lambda record: record.ref.identity))


class ConanBackend:
    """One local cache or one configured remote; callers coordinate other publishers.

    Only internally generated recipes are accepted. Package contents use Conan
    binaries without settings/options; dependency selection uses root lockfiles.
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
    def _read(self) -> Iterator[ConanReader]:
        with self._lock:
            if self._closed:
                raise BackendUnavailable("Conan backend is closed")
            yield ConanReader(self)

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def _locate(self, ref: Ref) -> tuple[str, Path]:
        logical = to_conan_reference(ref)
        revisions = self._client.index(f"{logical}#*").get(logical)
        if revisions is None or not revisions.revisions:
            raise NotFound(ref.identity)
        if len(revisions.revisions) != 1:
            raise IdentityConflict(f"Multiple recipe revisions for {logical}")
        pinned = f"{logical}#{next(iter(revisions.revisions))}"
        return pinned, self._client.cache_path(pinned)

    def get_many(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        with self._read():
            found = {}
            for ref in dict.fromkeys(normalize_ref(ref) for ref in refs):
                _, directory = self._locate(ref)
                found[ref] = read_recipe(directory, ref)[0]
            return tuple(found[normalize_ref(ref)] for ref in refs)

    def search(self, query: SearchQuery) -> tuple[Record, ...]:
        query = normalize_query(query)
        with self._read():
            pattern = search_pattern(query)
            if pattern is None:
                return ()
            candidates = self._client.index(pattern)
            refs = tuple(from_conan_reference(value) for value in candidates)
            records = self.get_many(refs)
            return tuple(
                sorted((r for r in records if query.matches(r)), key=lambda r: r.ref.identity)
            )

    def _closure(self, ref: Ref) -> GraphResult:
        graph, records = self._load_graph(ref)
        return GraphResult(
            tuple(sorted(records.values(), key=lambda record: record.ref.identity)),
            _graph_dependencies(graph, records),
        )

    def _load_graph(self, ref: Ref) -> tuple[NativeGraph, dict[Ref, Record]]:
        _, directory = self._locate(ref)
        root, _, lockfile = read_recipe(directory, ref)
        graph = self._client.graph(directory)
        root_package = graph.root.conanfile
        if _package_ref(root_package) != ref:
            raise InvalidPackage("Conan graph root differs from the requested reference")
        _validate_package(root_package)
        records = {ref: root}
        locked = lock_references(lockfile)
        for package in root_package.dependencies.host.topological_sort.values():
            _validate_package(package)
            pinned = package.ref.repr_notime()
            if pinned not in locked:
                raise InvalidPackage(f"Dependency is not pinned by root lockfile: {pinned}")
            package_ref = _package_ref(package)
            published, folder = self._locate(package_ref)
            if published != pinned:
                raise IdentityConflict(f"Locked revision differs from published identity: {pinned}")
            records[package_ref] = read_recipe(folder, package_ref)[0]
        return graph, records

    def publish(
        self, records: tuple[Record, ...], *, dependencies: tuple[Dependency, ...] = ()
    ) -> None:
        self._publish(records, dependencies)

    def publish_file(self, path: Path) -> Record:
        manifest = read_manifest(path)
        with self._read():
            self._publish((manifest.record,), manifest.dependencies, manifest=manifest)
            return self.get_many((manifest.record.ref,))[0]

    def _publish(
        self,
        records: tuple[Record, ...],
        dependencies: tuple[Dependency, ...],
        *,
        manifest: Manifest | None = None,
    ) -> None:
        records = tuple(normalize_record(record) for record in records)
        if any(record.files for record in records) and manifest is None:
            raise InvalidPublication("Publish stored files using a package JSON document")
        dependencies = tuple(normalize_dependency(edge) for edge in dependencies)
        _validate_coordinates(records, dependencies)
        with self._read():
            existing = {from_conan_reference(value) for value in self._client.index("*.*.*/*#*")}
            validate(records, dependencies, existing)
            new_refs = {record.ref for record in records}
            external = {edge.target for edge in dependencies if edge.target not in new_refs}
            for ref in sorted(external, key=lambda ref: ref.identity):
                self._closure(ref)
            with TemporaryDirectory(prefix="hwcatalog-conan-publish-") as temporary:
                prepared = self._prepare(Path(temporary), records, dependencies, manifest)
                self._publish_prepared(prepared)

    def _prepare(
        self,
        temporary: Path,
        records: tuple[Record, ...],
        dependencies: tuple[Dependency, ...],
        manifest: Manifest | None,
    ) -> tuple[PreparedRecipe, ...]:
        order: nx.DiGraph[Ref] = nx.DiGraph()
        order.add_nodes_from(record.ref for record in records)
        new_refs = {record.ref for record in records}
        order.add_edges_from(
            (edge.target, edge.source) for edge in dependencies if edge.target in new_refs
        )
        by_ref = {record.ref: record for record in records}
        prepared: dict[Ref, PreparedRecipe] = {}
        for index, ref in enumerate(
            nx.lexicographical_topological_sort(order, key=lambda r: r.identity)
        ):
            directory = temporary / f"recipe-{index}"
            targets = tuple(edge.target for edge in dependencies if edge.source == ref)
            write_recipe(
                directory,
                by_ref[ref],
                targets,
                source_path=manifest.source_path if manifest else None,
                files=manifest.files if manifest else None,
            )
            self._seed_lockfile(directory, targets, prepared)
            prepared[ref] = self._build_recipe(directory, ref)
        return tuple(prepared.values())

    def _build_recipe(self, directory: Path, ref: Ref) -> PreparedRecipe:
        self._client.graph(directory)
        self._client.create_lockfile(directory, seed=directory / "conan.lock")
        pinned = self._client.export(directory)
        exported = self._client.cache_path(pinned, download=False)
        _, metadata, _ = read_recipe(exported, ref)
        if metadata.has_source:
            shutil.copyfile(exported / "conandata.yml", directory / "conandata.yml")
        # The first export captured SCM. Reuse those coordinates during binary export.
        (directory / "source-path.txt").unlink(missing_ok=True)
        self._client.graph(directory)
        self._client.export_package(directory)
        return PreparedRecipe(ref, directory, pinned)

    def _seed_lockfile(
        self, directory: Path, targets: tuple[Ref, ...], prepared: dict[Ref, PreparedRecipe]
    ) -> None:
        locked: set[str] = set()
        for target in targets:
            if target in prepared:
                folder, pinned = prepared[target].directory, prepared[target].reference
            else:
                pinned, folder = self._locate(target)
            _, _, lockfile = read_recipe(folder, target)
            locked.add(pinned)
            locked.update(lock_references(lockfile))
        seed = Lockfile(version="0.5", requires=tuple(sorted(locked)))
        (directory / "conan.lock").write_text(seed.model_dump_json())

    def _publish_prepared(self, prepared: tuple[PreparedRecipe, ...]) -> None:
        published: list[Ref] = []
        for recipe in prepared:
            try:
                self._client.upload(recipe.reference)
            except (BackendUnavailable, InvalidPackage) as exc:
                raise PartialPublicationError(tuple(published), recipe.record_ref) from exc
            published.append(recipe.record_ref)

    def materialize(self, ref: Ref, *, with_sources: bool = False) -> MaterializedPackage:
        """Install artifacts and optionally run Conan source retrieval for this package."""
        with self._read():
            pinned, directory = self._locate(normalize_ref(ref))
            record, metadata, locked = read_recipe(directory, ref)
            if not record.source and not record.files:
                raise MaterializationError("Package has no stored source or artifacts")
            self._load_graph(ref)
            with TemporaryDirectory(prefix="hwcatalog-conan-install-") as temporary:
                lock = Path(temporary) / "conan.lock"
                lock.write_text(
                    Lockfile(
                        version="0.5", requires=tuple(sorted({*lock_references(locked), pinned}))
                    ).model_dump_json()
                )
                result = self._client.install(
                    pinned, lock, Path(temporary), with_sources=with_sources
                )
                package = result.root.conanfile.dependencies.host[to_conan_reference(ref)]
                if package.package_folder is None:
                    raise MaterializationError("Conan did not install the data package")
                source_path = None
                if with_sources and record.source is not None:
                    if package.folders.source_folder is None:
                        raise MaterializationError("Conan did not provide the SCM source folder")
                    subfolder = "checkout" if metadata.format in (1, 2) else "source"
                    source_path = Path(package.folders.source_folder) / subfolder
                return MaterializedPackage(Path(package.package_folder), source_path)


def _validate_coordinates(
    records: tuple[Record, ...], dependencies: tuple[Dependency, ...]
) -> None:
    versions: dict[tuple[Ref, str], Ref] = {}
    for record in records:
        to_conan_reference(record.ref)
    for edge in dependencies:
        to_conan_reference(edge.source)
        to_conan_reference(edge.target)
        key = (edge.source, edge.target.family)
        if versions.setdefault(key, edge.target) != edge.target:
            raise VersionConflict("Conan requires one version per direct dependency family")


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
    return tuple(sorted(edges, key=lambda edge: edge.to_json()))
