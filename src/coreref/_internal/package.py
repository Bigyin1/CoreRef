"""Operations on one local package or one published package reference."""

from __future__ import annotations

from dataclasses import replace
from functools import cached_property
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from ..api.errors import Conflict, InvalidPackage, NotFound
from ..api.packages.records import (
    Dependency,
    InstalledRecord,
    LocalRecord,
    Record,
    Ref,
    Requirement,
)
from .graph import GraphData, ResolvedConanGraph
from .recipe import ConsumerRecipe, LocalRecipe, StoredRecipe
from .references import from_conan_reference, to_conan_reference

if TYPE_CHECKING:
    from ..api.packages.catalog import Catalog


class StoredPackage:
    """Resolve or install one package, reusing its loaded recipe."""

    def __init__(self, catalog: Catalog, ref: Ref) -> None:
        to_conan_reference(ref)
        self.ref = ref
        self._catalog = catalog
        self._client = catalog._client
        self._recipe: StoredRecipe | None = None

    def recipe(self) -> StoredRecipe:
        if self._recipe is None:
            reference = str(self._resolve_ref())
            directory = self._client.download_recipe(reference)
            self._recipe = StoredRecipe(directory, reference=reference)
        return self._recipe

    def resolved_graph(self, lockfile: Path | None) -> PackageGraph:
        """Resolve the stored root itself, retaining its exact revision and metadata."""
        with TemporaryDirectory(prefix="coreref-resolve-") as temporary:
            directory = Path(temporary)
            ConsumerRecipe((str(self.ref),)).write(directory)
            native = self._client.graph(
                directory,
                lockfile=lockfile,
                update=(
                    self.ref.revision is None
                    and lockfile is None
                    and self._client.remote is not None
                ),
            )
        graph = ResolvedConanGraph(native)
        root = from_conan_reference(graph.direct_references[0])
        recipes = _read_graph_recipes(graph, cached=self._recipe)
        self._recipe = recipes[root]
        return PackageGraph(self._catalog, self._recipe.record, graph, recipes=recipes)

    def get(self, *, with_sources: bool = False) -> InstalledRecord:
        if self._client.editable(str(self.ref)) is not None:
            working_graph = self.resolved_graph(None)
            return working_graph.get((self.ref,), with_sources=with_sources)[0]
        recipe = self.recipe()
        fetch_source = with_sources and recipe.has_source
        if not recipe.record.file_names and not fetch_source:
            return InstalledRecord(recipe.record)
        with TemporaryDirectory(prefix="coreref-install-") as temporary:
            graph = ResolvedConanGraph(
                self._client.install(recipe.reference, Path(temporary), with_sources=fetch_source)
            )
            ref = graph.package_ref(self.ref)
            recipe = _read_graph_recipes(graph, cached=recipe)[ref]
            return recipe.installed(graph.package(ref), with_sources=fetch_source)

    def _resolve_ref(self) -> Ref:
        ref = self.ref
        if ref.revision is not None:
            return ref
        revisions = self._client.index(f"{ref}*#latest").get(str(ref))
        if revisions is None or not revisions.revisions:
            raise NotFound(ref)
        revision = next(iter(revisions.revisions))
        return from_conan_reference(f"{ref}#{revision}")


def _read_graph_recipes(
    graph: ResolvedConanGraph, *, cached: StoredRecipe | None = None
) -> dict[Ref, StoredRecipe]:
    """Read each selected recipe from the node already loaded by Conan."""
    recipes: dict[Ref, StoredRecipe] = {}
    for reference in dict.fromkeys(graph.references):
        ref = from_conan_reference(reference)
        package = graph.package(ref)
        directory = package.recipe_folder
        if directory is None:
            raise InvalidPackage(f"Conan did not provide the recipe directory for {ref}")
        editable = reference in graph.editable_references
        if cached is not None and cached.record.ref == ref and cached.is_editable == editable:
            recipes[ref] = cached
        else:
            recipes[ref] = StoredRecipe(
                Path(directory), reference=str(ref), native=package if editable else None
            )
    return recipes


class PackageGraph:
    """A resolved local or published root with shared recipe and installation caches."""

    def __init__(
        self,
        catalog: Catalog,
        record: LocalRecord | Record,
        graph: ResolvedConanGraph,
        *,
        recipes: dict[Ref, StoredRecipe],
    ) -> None:
        self.record = record
        self.graph = graph
        self._client = catalog._client
        self._recipes = recipes
        self._fetched: dict[Ref, InstalledRecord] = {}

        self.result: GraphData = graph.result(
            record, tuple(recipe.record for recipe in recipes.values())
        )
        editables = tuple(
            recipe.installed(graph.package(ref), with_sources=True)
            for ref, recipe in recipes.items()
            if recipe.is_editable
        )
        if any(
            record.source is not None and record.source.is_relative_to(self._client.cache_directory)
            for record in editables
        ):
            raise InvalidPackage("Working package sources point into package storage")
        self._fetched.update((record.ref, record) for record in editables)
        self._sources = {
            record.ref: record.source for record in editables if record.source is not None
        }

    def create_lockfile(self, path: str | Path) -> Path:
        output = Path(path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        self._client.save_lockfile(self.graph.native, output)
        return output

    def get_sources(self, refs: tuple[Ref, ...]) -> dict[Ref, Path]:
        selected = self._select(refs)
        missing = tuple(
            ref for ref in selected if ref not in self._sources and self._recipes[ref].has_source
        )
        if missing:
            self._client.install_sources(self.graph.native, frozenset(map(str, missing)))
            self._sources.update(
                {
                    ref: self._recipes[ref].source_directory(self.graph.package(ref))
                    for ref in missing
                }
            )
        return {ref: self._sources[ref] for ref in selected if ref in self._sources}

    def _select(self, refs: tuple[Ref, ...]) -> tuple[Ref, ...]:
        selected = tuple(dict.fromkeys(self.resolve_ref(ref) for ref in refs))
        if any(ref not in self._recipes for ref in selected):
            raise InvalidPackage("Only published dependencies of this graph can be fetched")
        return selected

    def get(
        self, refs: tuple[Ref, ...], *, with_sources: bool = False
    ) -> tuple[InstalledRecord, ...]:
        selected = self._select(refs)
        missing = tuple(
            ref for ref in selected if self._needs_fetch(ref, with_sources=with_sources)
        )
        if missing:
            recipes = tuple(self._recipes[ref] for ref in missing)
            required = tuple(
                recipe
                for recipe in recipes
                if recipe.record.file_names or (with_sources and recipe.has_source)
            )
            records = {
                recipe.record.ref: InstalledRecord(recipe.record)
                for recipe in recipes
                if not recipe.record.file_names
            }
            if required:
                records.update(
                    (record.ref, record)
                    for record in self._install(required, with_sources=with_sources)
                )
            self._fetched.update(records)
            self._sources.update(
                (ref, record.source) for ref, record in records.items() if record.source is not None
            )
        return tuple(self._fetched[ref] for ref in selected)

    @cached_property
    def _resolved_refs(self) -> dict[Ref, Ref]:
        return {record.ref.without_revision(): record.ref for record in self.result[0]}

    def resolve_ref(self, ref: Ref) -> Ref:
        """Match a requested reference against this graph's fixed revision selection."""
        resolved = self._resolved_refs.get(ref.without_revision())
        if resolved is None:
            raise InvalidPackage(f"Unknown reference in dependency graph: {ref}")
        if ref.revision is not None and resolved.revision != ref.revision:
            raise InvalidPackage(f"Revision is not part of this graph: {ref}")
        return resolved

    def _needs_fetch(self, ref: Ref, *, with_sources: bool) -> bool:
        record = self._fetched.get(ref)
        if record is None:
            return True
        return with_sources and not record._sources_ready

    def _install(
        self, recipes: tuple[StoredRecipe, ...], *, with_sources: bool
    ) -> tuple[InstalledRecord, ...]:
        selected = {recipe.record.ref for recipe in recipes if with_sources and recipe.has_source}
        sources = {str(ref): ref in selected for ref in self._recipes}
        with TemporaryDirectory(prefix="coreref-batch-") as temporary:
            directory = Path(temporary)
            ConsumerRecipe(tuple(recipe.reference for recipe in recipes)).write(directory)
            self._client.save_lockfile(self.graph.native, directory / "conan.lock")
            installed = ResolvedConanGraph(self._client.install_project(directory, sources))
            self._check_install(installed, recipes)
            return tuple(
                recipe.installed(
                    installed.package(recipe.record.ref),
                    with_sources=with_sources and recipe.has_source,
                )
                for recipe in recipes
            )

    def _check_install(
        self, installed: ResolvedConanGraph, recipes: tuple[StoredRecipe, ...]
    ) -> None:
        references = set(installed.references)
        if installed.editable_references != self.graph.editable_references.intersection(references):
            raise Conflict("Workspace changed; resolve the project again")
        if not references.issubset(self.graph.references) or not all(
            recipe.reference in references for recipe in recipes
        ):
            raise Conflict("Installed dependencies differ from the resolved graph")


class LocalPackage:
    """One working local record and a fixed set of requirements for this operation."""

    def __init__(
        self,
        catalog: Catalog,
        record: LocalRecord,
        *,
        dependencies: tuple[Dependency, ...],
        lockfile: Path | None,
    ) -> None:
        self._catalog = catalog
        self._client = catalog._client
        self.lockfile = lockfile.expanduser().absolute() if lockfile is not None else None
        self.record = self._prepare(record)
        self.dependencies = self._references(dependencies)

    @cached_property
    def graph(self) -> ResolvedConanGraph:
        recipe = LocalRecipe(self.record, tuple(str(ref) for ref in self.dependencies))
        with TemporaryDirectory(prefix="coreref-resolve-") as temporary:
            directory = Path(temporary)
            recipe.write_for_resolution(directory)
            update = (
                self.lockfile is None
                and self._client.remote is not None
                and any(
                    isinstance(dep, Requirement) or dep.revision is None
                    for dep in self.dependencies
                )
            )
            native = self._client.graph(directory, lockfile=self.lockfile, update=update)
            return ResolvedConanGraph(native)

    @cached_property
    def resolved(self) -> PackageGraph:
        recipes = _read_graph_recipes(self.graph)
        return PackageGraph(self._catalog, self.record, self.graph, recipes=recipes)

    def pack(
        self, *, resolved: ResolvedConanGraph | None = None, record: LocalRecord | None = None
    ) -> InstalledRecord:
        record = self._publication_record(self.record if record is None else record)
        if resolved is None and self.dependencies:
            resolved = self.resolved.graph
        recipe = LocalRecipe(record, tuple(str(dep) for dep in self.dependencies))
        with TemporaryDirectory(prefix="coreref-pack-") as temporary:
            directory = Path(temporary)
            recipe.write(directory)
            recipe.copy_files(directory)
            lockfile = self.lockfile
            if resolved is not None:
                lockfile = directory / "conan.lock"
                self._client.save_lockfile(resolved.native, lockfile)
            return self._export(directory, lockfile=lockfile)

    def _prepare(self, record: LocalRecord) -> LocalRecord:
        if not isinstance(record, LocalRecord):
            raise InvalidPackage("Expected one LocalRecord")
        to_conan_reference(record.ref)
        if record.ref.revision is not None:
            raise InvalidPackage("A local package root must not specify a revision")
        if record.source is not None:
            record.source = record.source.expanduser().absolute()
        for name, path in record.files.items():
            record.files[name] = path.expanduser().absolute()
        return record

    def _references(self, dependencies: tuple[Dependency, ...]) -> tuple[Dependency, ...]:
        if any(not isinstance(dep, (Ref, Requirement)) for dep in dependencies):
            raise InvalidPackage("Dependencies must be Ref or Requirement values")
        logical = {dep.without_revision() if isinstance(dep, Ref) else dep for dep in dependencies}
        if len(logical) != len(dependencies):
            raise InvalidPackage("Duplicate dependency")
        if self.record.ref in logical:
            raise InvalidPackage("A package cannot depend on itself")
        return tuple(dependencies)

    def _publication_record(self, record: LocalRecord) -> LocalRecord:
        files: dict[str, Path] = {}
        for name, path in record.files.items():
            local = self._local_path(path)
            if not local.is_file():
                raise InvalidPackage(f"Artifact is not a regular file: {path}")
            if any(name.startswith(other + "/") or other.startswith(name + "/") for other in files):
                raise InvalidPackage("Artifact paths cannot overlap")
            files[name] = local
        source = record.source
        if source is not None:
            source = self._local_path(source)
            if not source.is_dir():
                raise InvalidPackage("Git source path must be a repository directory")
        return replace(record, source=source, files=files)

    def _export(self, directory: Path, *, lockfile: Path | None) -> InstalledRecord:
        native = self._client.export_package(directory, lockfile=lockfile)
        reference = native.root.ref
        if reference is None:
            raise InvalidPackage("Conan did not return the exported recipe reference")
        pinned = reference.repr_notime()
        recipe = StoredRecipe(self._client.cache_path(pinned), reference=pinned)
        return recipe.installed(native.root.conanfile, with_sources=False)

    @staticmethod
    def _local_path(path: Path) -> Path:
        try:
            return path.expanduser().resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise InvalidPackage(f"Publication input does not exist: {path}") from exc
