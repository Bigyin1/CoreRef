"""Conan recipe storage with exact root lockfiles and nontransactional publication."""

import shutil
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

import networkx as nx

from ...api.capabilities import CONAN_CAPABILITIES
from ...api.enums import Direction, RelationKind
from ...api.errors import (
    BackendUnavailable,
    IdentityConflict,
    InvalidPackage,
    NotFound,
    PartialPublicationError,
    UnsupportedOperation,
    VersionConflict,
)
from ...api.graph import CatalogGraph
from ...api.models.graph import GraphResult, Neighbor, Relation
from ...api.models.query import SearchQuery
from ...api.models.records import Record, Ref
from ..validation import (
    normalize_query,
    normalize_record,
    normalize_ref,
    normalize_relation,
    validate,
)
from .conan_cli import ConanCLI, GraphData
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

    def neighbors(
        self, ref: Ref, kind: RelationKind, direction: Direction, filters: SearchQuery
    ) -> tuple[Neighbor, ...]:
        if kind != RelationKind.REQUIRES:
            raise UnsupportedOperation(f"Conan does not support {kind.value}")
        self.backend.get_many((ref,))
        if direction == Direction.OUTGOING:
            graph = self.backend._closure(ref)
            records = {record.ref: record for record in graph.records}
            return tuple(
                Neighbor(edge, records[edge.target])
                for edge in graph.relations
                if edge.source == ref and filters.matches(records[edge.target])
            )
        result: list[Neighbor] = []
        for candidate in self.search(filters):
            result.extend(
                Neighbor(edge, candidate)
                for edge in self.backend._closure(candidate.ref).relations
                if edge.source == candidate.ref and edge.target == ref
            )
        return tuple(sorted(result, key=lambda neighbor: neighbor.relation.to_json()))


class ConanGraph(CatalogGraph):
    def __init__(self, backend: ConanBackend) -> None:
        super().__init__(backend._read, CONAN_CAPABILITIES)
        self.backend = backend

    def dependency_closure(self, ref: Ref, *, resolve_versions: bool = False) -> GraphResult:
        """Use Conan's graph with the selected root's exported lockfile."""
        if resolve_versions:
            raise UnsupportedOperation("Conan uses lockfiles, not hwcatalog compatibility edges")
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
                graph = self.backend._closure(candidate.ref)
                if _depends_on(graph, candidate.ref, ref, transitive=transitive):
                    result.append(candidate)
            return tuple(sorted(result, key=lambda record: record.ref.identity))


def _depends_on(graph: GraphResult, source: Ref, target: Ref, *, transitive: bool) -> bool:
    if transitive:
        return any(record.ref == target for record in graph.records)
    return any(edge.source == source and edge.target == target for edge in graph.relations)


class ConanBackend:
    """One local cache or one configured remote; callers coordinate other publishers.

    Recipes are trusted executable Conan code. Version one only accepts the
    canonical format emitted by ``write_recipe``. No binary packages are needed.
    """

    capabilities = CONAN_CAPABILITIES

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

    def _closure(self, ref: Ref) -> GraphResult:
        _, directory = self._locate(ref)
        root, _, lockfile = read_recipe(directory, ref)
        graph = self._cli.graph(directory)
        return self._convert_graph(graph, root, lockfile)

    def _convert_graph(self, graph: GraphData, root: Record, lockfile: Lockfile) -> GraphResult:
        if len(graph.root) != 1:
            raise InvalidPackage("Conan graph must have exactly one root")
        root_id = next(iter(graph.root))
        if root_id not in graph.nodes:
            raise InvalidPackage("Conan graph root is absent from nodes")
        records = {root_id: root}
        locked = lock_references(lockfile)
        for node_id, node in graph.nodes.items():
            if node.context != "host" or node.settings or node.options:
                raise InvalidPackage("Configurable recipes and build dependencies are unsupported")
            if node_id != root_id:
                if node.ref not in locked:
                    raise InvalidPackage(f"Dependency is not pinned by root lockfile: {node.ref}")
                ref = from_conan_reference(node.ref)
                pinned, directory = self._locate(ref)
                if pinned != node.ref:
                    raise IdentityConflict(
                        f"Locked revision differs from published identity: {node.ref}"
                    )
                records[node_id] = read_recipe(directory, ref)[0]
        relations = _graph_relations(graph, records)
        return GraphResult(
            tuple(
                sorted({r.ref: r for r in records.values()}.values(), key=lambda r: r.ref.identity)
            ),
            relations,
            consistency="live",
        )

    def publish(self, records: tuple[Record, ...], *, relations: tuple[Relation, ...] = ()) -> None:
        records = tuple(normalize_record(record) for record in records)
        relations = tuple(normalize_relation(edge) for edge in relations)
        _validate_supported(records, relations)
        with self._read():
            existing = {from_conan_reference(value) for value in self._cli.index("*.*.*/*#*")}
            validate(records, relations, existing)
            new_refs = {record.ref for record in records}
            external = {edge.target for edge in relations if edge.target not in new_refs}
            for ref in sorted(external, key=lambda ref: ref.identity):
                self._closure(ref)
            with TemporaryDirectory(prefix="hwcatalog-conan-publish-") as temporary:
                prepared = self._prepare(Path(temporary), records, relations)
                self._publish_prepared(prepared)

    def _prepare(
        self, temporary: Path, records: tuple[Record, ...], relations: tuple[Relation, ...]
    ) -> tuple[PreparedRecipe, ...]:
        # Validate every graph in a private cache before exposing any new records.
        stage_home = temporary / "home"
        shutil.copytree(self._cli.home, stage_home)
        stage = ConanCLI(stage_home, self._cli.executable, self._cli.remote, self._cli.timeout)
        dependencies: nx.DiGraph[Ref] = nx.DiGraph()
        dependencies.add_nodes_from(record.ref for record in records)
        new_refs = {record.ref for record in records}
        dependencies.add_edges_from(
            (edge.target, edge.source) for edge in relations if edge.target in new_refs
        )
        by_ref = {record.ref: record for record in records}
        prepared: dict[Ref, PreparedRecipe] = {}
        for index, ref in enumerate(
            nx.lexicographical_topological_sort(dependencies, key=lambda r: r.identity)
        ):
            directory = temporary / f"recipe-{index}"
            targets = tuple(edge.target for edge in relations if edge.source == ref)
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


def _validate_supported(records: tuple[Record, ...], relations: tuple[Relation, ...]) -> None:
    versions: dict[tuple[Ref, str], Ref] = {}
    for record in records:
        to_conan_reference(record.ref)
    for edge in relations:
        to_conan_reference(edge.source)
        to_conan_reference(edge.target)
        if (
            edge.kind != RelationKind.REQUIRES
            or edge.alias is not None
            or edge.ordinal is not None
            or edge.metadata
        ):
            raise UnsupportedOperation(
                "Conan supports only plain REQUIRES without binding attributes"
            )
        key = (edge.source, edge.target.family)
        if versions.setdefault(key, edge.target) != edge.target:
            raise VersionConflict("Conan requires one version per direct dependency family")


def _graph_relations(graph: GraphData, records: dict[str, Record]) -> tuple[Relation, ...]:
    result = set()
    for source, node in graph.nodes.items():
        for target, requirement in node.dependencies.items():
            if requirement.build:
                raise InvalidPackage("Build requirements are unsupported")
            if requirement.direct:
                if source not in records or target not in records:
                    raise InvalidPackage("Conan graph contains an unknown endpoint")
                result.add(
                    Relation(
                        records[source].ref, records[target].ref, RelationKind.REQUIRES
                    ).to_json()
                )
    return tuple(Relation.from_json(value) for value in sorted(result))
