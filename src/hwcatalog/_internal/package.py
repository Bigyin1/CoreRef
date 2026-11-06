"""Operations on one local package or one published package reference."""

import json
from dataclasses import replace
from functools import cached_property
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Self

from frozendict import deepfreeze, frozendict

from ..api.errors import Conflict, InvalidPackage, NotFound
from ..api.query import SearchQuery
from ..api.records import LocalRecord, Record, Ref
from .client import RecipeRevisions
from .graph import GraphData, ResolvedConanGraph
from .recipe import LocalRecipe, StoredRecipe
from .references import from_conan_reference, to_conan_reference

if TYPE_CHECKING:
    from ..api.catalog import Catalog


class StoredPackage:
    """A published Ref with recipes retained only for the current operation."""

    def __init__(self, ref: Ref) -> None:
        to_conan_reference(ref)
        self.ref = ref
        self._catalog = ref._catalog
        self._client = self._catalog._client
        self._recipes: dict[Ref, StoredRecipe] = {}

    def load(self, revisions: RecipeRevisions) -> Self:
        """Fetch this package using revisions already obtained from the index."""
        self._download(self.ref, revisions.pin(str(self.ref)))
        return self

    def recipe(self) -> StoredRecipe:
        return self._load(str(self.ref))

    def record_at(self, reference: str) -> Record:
        """Read an exact requirement and reject a changed published revision."""
        return self._load(reference).record

    def get(self) -> Record:
        return self._get(with_sources=False)

    def get_with_sources(self) -> Record:
        return self._get(with_sources=True)

    def dependencies(self, query: SearchQuery) -> tuple[Record, ...]:
        records = self._read_records(self.recipe().requirements)
        return query.select(records)

    def find_dependents(self, query: SearchQuery, *, transitive: bool) -> tuple[Record, ...]:
        recipes = tuple(package.recipe() for package in self._catalog._packages(query))
        self._recipes.update((recipe.record.ref, recipe) for recipe in recipes)
        self.recipe()
        records = (
            recipe.record
            for recipe in recipes
            if recipe.record.ref != self.ref
            and query.matches(recipe.record)
            and self._depends_on(recipe, transitive=transitive)
        )
        return tuple(sorted(records, key=lambda record: record.ref))

    def _depends_on(self, recipe: StoredRecipe, *, transitive: bool) -> bool:
        if not transitive:
            return any(record.ref == self.ref for record in self._read_records(recipe.requirements))
        graph = ResolvedConanGraph(self._client.graph(recipe.directory))
        graph.result(recipe.record, self._read_records(graph.references))
        return graph.contains(self.ref)

    def _get(self, *, with_sources: bool) -> Record:
        recipe = self.recipe()
        fetch_source = with_sources and recipe.has_source
        if not recipe.record.files and not fetch_source:
            return recipe.record
        with TemporaryDirectory(prefix="hwcatalog-conan-install-") as temporary:
            graph = ResolvedConanGraph(
                self._client.install(recipe.reference, Path(temporary), with_sources=fetch_source)
            )
            self._read_records(graph.references)
            return recipe.installed(graph.package(self.ref), with_sources=fetch_source)

    def _read_records(self, references: tuple[str, ...]) -> tuple[Record, ...]:
        return tuple(self._load(reference).record for reference in dict.fromkeys(references))

    def _load(self, reference: str) -> StoredRecipe:
        ref = from_conan_reference(reference, catalog=self._catalog)
        recipe = self._recipes.get(ref)
        if recipe is None:
            revisions = self._client.index(f"{ref}*#*").get(str(ref))
            if revisions is None:
                raise NotFound(ref)
            recipe = self._download(ref, revisions.pin(str(ref)))
        if "#" in reference and reference != recipe.reference:
            raise Conflict(f"Locked revision differs from published identity: {reference}")
        return recipe

    def _download(self, ref: Ref, reference: str) -> StoredRecipe:
        directory = self._client.download_recipe(reference)
        recipe = StoredRecipe(directory, ref, reference=reference)
        self._recipes[ref] = recipe
        return recipe


class LocalPackage:
    """A detached local description and dependencies for one catalog operation."""

    def __init__(
        self,
        catalog: Catalog,
        record: LocalRecord,
        *,
        dependencies: tuple[Ref, ...],
        lockfile: Path | None,
    ) -> None:
        self._catalog = catalog
        self._client = catalog._client
        self.record = self._snapshot(record)
        self.dependencies = self._references(dependencies)
        self.lockfile = lockfile
        self._packages = {ref: StoredPackage(ref) for ref in self.dependencies}
        self._fetched: dict[Ref, Record] = {}

    @cached_property
    def graph(self) -> ResolvedConanGraph:
        recipe = LocalRecipe(self.record, self._requirements())
        with TemporaryDirectory(prefix="hwcatalog-conan-resolve-") as temporary:
            directory = Path(temporary)
            recipe.write(directory)
            return ResolvedConanGraph(self._client.graph(directory, lockfile=self.lockfile))

    @cached_property
    def result(self) -> GraphData:
        return self.graph.result(self.record, self._read_records(self.graph.references))

    def create_lockfile(self, path: str | Path) -> Path:
        output = Path(path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        self._client.save_lockfile(self.graph.native, output)
        return output

    def get(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        return self._get(refs, with_sources=False)

    def get_with_sources(self, refs: tuple[Ref, ...]) -> tuple[Record, ...]:
        return self._get(refs, with_sources=True)

    def _get(self, refs: tuple[Ref, ...], *, with_sources: bool) -> tuple[Record, ...]:
        selected = tuple(dict.fromkeys(refs))
        if any(ref not in self._packages for ref in selected):
            raise InvalidPackage("Only published dependencies of this graph can be fetched")
        missing = tuple(
            ref for ref in selected if self._needs_fetch(ref, with_sources=with_sources)
        )
        if missing:
            recipes = tuple(self._packages[ref].recipe() for ref in missing)
            required = tuple(
                recipe
                for recipe in recipes
                if recipe.record.files or (with_sources and recipe.has_source)
            )
            records = {recipe.record.ref: recipe.record for recipe in recipes}
            if required:
                records.update(
                    (record.ref, record)
                    for record in self._install(required, with_sources=with_sources)
                )
            self._fetched.update(records)
        return tuple(self._fetched[ref] for ref in selected)

    def _needs_fetch(self, ref: Ref, *, with_sources: bool) -> bool:
        record = self._fetched.get(ref)
        if record is None:
            return True
        return with_sources and record.source is None and self._packages[ref].recipe().has_source

    def _install(
        self, recipes: tuple[StoredRecipe, ...], *, with_sources: bool
    ) -> tuple[Record, ...]:
        selected = {recipe.record.ref for recipe in recipes if with_sources and recipe.has_source}
        sources = {str(ref): ref in selected for ref in self._packages}
        with TemporaryDirectory(prefix="hwcatalog-conan-batch-") as temporary:
            directory = Path(temporary)
            LocalRecipe(self.record, tuple(recipe.reference for recipe in recipes)).write(directory)
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
        if not references.issubset(self.graph.references) or not all(
            recipe.reference in references for recipe in recipes
        ):
            raise Conflict("Installed dependencies differ from the resolved graph")

    def publish(self) -> Record:
        record = self._publication_record()
        logical = to_conan_reference(record.ref)
        if logical in self._client.index(f"{logical}*#*"):
            raise Conflict("Record identity already published")
        recipe = LocalRecipe(record, self._requirements())
        with TemporaryDirectory(prefix="hwcatalog-conan-publish-") as temporary:
            directory = Path(temporary)
            recipe.write(directory)
            recipe.copy_files(directory)
            return self._export(directory)

    def _snapshot(self, record: LocalRecord) -> LocalRecord:
        if not isinstance(record, LocalRecord):
            raise InvalidPackage("Expected one LocalRecord")
        source = record.source.expanduser().absolute() if record.source is not None else None
        return LocalRecord(
            Ref(**record.ref.to_dict(), catalog=self._catalog),
            deepfreeze(json.loads(record.to_json())["payload"]),
            source,
            frozendict(record.files),
        )

    def _references(self, dependencies: tuple[Ref, ...]) -> tuple[Ref, ...]:
        if any(not isinstance(ref, Ref) for ref in dependencies):
            raise InvalidPackage("Dependencies must be Ref values")
        refs = tuple(Ref(**ref.to_dict(), catalog=self._catalog) for ref in dependencies)
        if len(refs) != len(set(refs)):
            raise InvalidPackage("Duplicate dependency")
        if self.record.ref in refs:
            raise InvalidPackage("A package cannot depend on itself")
        return refs

    def _requirements(self) -> tuple[str, ...]:
        return tuple(self._packages[ref].recipe().reference for ref in self.dependencies)

    def _read_records(self, references: tuple[str, ...]) -> tuple[Record, ...]:
        records = []
        for reference in dict.fromkeys(references):
            ref = from_conan_reference(reference, catalog=self._catalog)
            if ref not in self._packages:
                self._packages[ref] = StoredPackage(ref)
            records.append(self._packages[ref].record_at(reference))
        return tuple(records)

    def _publication_record(self) -> LocalRecord:
        files: dict[str, Path] = {}
        for name, path in self.record.files.items():
            local = self._local_path(path)
            if not local.is_file():
                raise InvalidPackage(f"Artifact is not a regular file: {path}")
            if any(name.startswith(other + "/") or other.startswith(name + "/") for other in files):
                raise InvalidPackage("Artifact paths cannot overlap")
            files[name] = local
        source = self.record.source
        if source is not None:
            source = self._local_path(source)
            if not source.is_dir():
                raise InvalidPackage("Git source path must be a repository directory")
        return replace(self.record, source=source, files=files)

    def _export(self, directory: Path) -> Record:
        reference = self._client.export_package(directory, lockfile=self.lockfile).root.ref
        if reference is None:
            raise InvalidPackage("Conan did not return the exported recipe reference")
        pinned = reference.repr_notime()
        recipe = StoredRecipe(self._client.cache_path(pinned), self.record.ref, reference=pinned)
        self._client.upload(pinned)
        return recipe.record

    @staticmethod
    def _local_path(path: Path) -> Path:
        try:
            return path.expanduser().resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise InvalidPackage(f"Publication input does not exist: {path}") from exc
