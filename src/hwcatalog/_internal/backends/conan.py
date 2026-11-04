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
    NotFound,
    PartialPublicationError,
    VersionConflict,
)
from ...api.graph import CatalogGraph
from ...api.models.graph import Dependency, GraphResult
from ...api.models.query import SearchQuery
from ...api.models.records import Record, Ref
from ...backends.conan_types import NativeGraph, NativePackage
from ..validation import (
    normalize_dependency,
    normalize_query,
    normalize_record,
    normalize_ref,
    validate,
)
from .conan_cli import ConanCLI
from .conan_format import (
    Lockfile,
    from_conan_reference,
    lock_references,
    read_recipe,
    search_pattern,
    to_conan_reference,
    write_recipe,
)


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

    Recipes are trusted executable Conan code. Version one only accepts the
    canonical format emitted by ``write_recipe``. No binary packages are needed.
    """

    def __init__(
        self,
        home: Path,
        *,
        remote: str | None = None,
        executable: str = "conan",
        timeout: float = 60,
    ) -> None:
        self._cli = ConanCLI(home, executable, remote, timeout)
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
        revisions = self._cli.index(f"{logical}#*").get(logical)
        if revisions is None or not revisions.revisions:
            raise NotFound(ref.identity)
        if len(revisions.revisions) != 1:
            raise IdentityConflict(f"Multiple recipe revisions for {logical}")
        pinned = f"{logical}#{next(iter(revisions.revisions))}"
        return pinned, self._cli.cache_path(pinned)

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
            candidates = self._cli.index(pattern)
            refs = tuple(from_conan_reference(value) for value in candidates)
            records = self.get_many(refs)
            return tuple(
                sorted((r for r in records if query.matches(r)), key=lambda r: r.ref.identity)
            )

    def load_graph(self, ref: Ref) -> NativeGraph:
        """Return the actual Conan DepsGraph, with its native dependency views.

        The graph is loaded using the root's exported lockfile. Consumers can use
        graph.root.conanfile.dependencies.host.topological_sort directly.
        """
        with self._read():
            graph, _ = self._load_graph(normalize_ref(ref))
            return graph

    def _closure(self, ref: Ref) -> GraphResult:
        graph, records = self._load_graph(ref)
        return GraphResult(
            tuple(sorted(records.values(), key=lambda record: record.ref.identity)),
            _graph_dependencies(graph, records),
        )

    def _load_graph(self, ref: Ref) -> tuple[NativeGraph, dict[Ref, Record]]:
        _, directory = self._locate(ref)
        root, _, lockfile = read_recipe(directory, ref)
        graph = self._cli.graph(directory)
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
        records = tuple(normalize_record(record) for record in records)
        dependencies = tuple(normalize_dependency(edge) for edge in dependencies)
        _validate_coordinates(records, dependencies)
        with self._read():
            existing = {from_conan_reference(value) for value in self._cli.index("*.*.*/*#*")}
            validate(records, dependencies, existing)
            new_refs = {record.ref for record in records}
            external = {edge.target for edge in dependencies if edge.target not in new_refs}
            for ref in sorted(external, key=lambda ref: ref.identity):
                self._closure(ref)
            with TemporaryDirectory(prefix="hwcatalog-conan-publish-") as temporary:
                prepared = self._prepare(Path(temporary), records, dependencies)
                self._publish_prepared(prepared)

    def _prepare(
        self, temporary: Path, records: tuple[Record, ...], dependencies: tuple[Dependency, ...]
    ) -> tuple[PreparedRecipe, ...]:
        # Validate every graph in a private cache before exposing any new records.
        stage_home = temporary / "home"
        shutil.copytree(self._cli.home, stage_home)
        stage = ConanCLI(stage_home, self._cli.executable, self._cli.remote, self._cli.timeout)
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
            write_recipe(directory, by_ref[ref], targets)
            self._seed_lockfile(directory, targets, prepared)
            stage.graph(directory)
            stage.create_lockfile(directory, seed=directory / "conan.lock")
            read_recipe(directory, ref)
            stage.graph(directory)
            pinned = stage.export(directory)
            prepared[ref] = PreparedRecipe(ref, directory, pinned)
        return tuple(prepared.values())

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
                pinned = self._cli.export(recipe.directory)
                if pinned != recipe.reference:
                    raise InvalidPackage(
                        "Recipe revision changed between validation and publication"
                    )
                self._cli.upload(pinned)
            except (BackendUnavailable, InvalidPackage) as exc:
                raise PartialPublicationError(tuple(published), recipe.record_ref) from exc
            published.append(recipe.record_ref)


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
