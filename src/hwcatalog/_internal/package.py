"""Operations on one local package or one published package reference."""

import json
import shutil
from dataclasses import replace
from functools import cached_property
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Self

from frozendict import deepfreeze, frozendict

from ..api.errors import CatalogError, Conflict, InvalidPackage, NotFound
from ..api.query import SearchQuery
from ..api.records import LocalRecord, Record, Ref
from .client import RecipeRevisions
from .graph import GraphData, ResolvedConanGraph
from .recipe import LocalRecipe, StoredRecipe
from .references import from_conan_reference, to_conan_reference
from .types import NativePackage

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

    @classmethod
    def from_index(cls, ref: Ref, revisions: RecipeRevisions) -> Self:
        package = cls(ref)
        package._download(ref, cls._pin(ref, revisions))
        return package

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
        records = self._read_records(self.recipe().metadata.requires)
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
            return any(
                record.ref == self.ref for record in self._read_records(recipe.metadata.requires)
            )
        graph = ResolvedConanGraph(self._client.graph(recipe.directory))
        graph.result(recipe.record, self._read_records(graph.references))
        return graph.contains(self.ref)

    def _get(self, *, with_sources: bool) -> Record:
        recipe = self.recipe()
        fetch_source = with_sources and recipe.metadata.has_source
        if not recipe.record.files and not fetch_source:
            return recipe.record
        with TemporaryDirectory(prefix="hwcatalog-conan-install-") as temporary:
            graph = ResolvedConanGraph(
                self._client.install(recipe.reference, Path(temporary), with_sources=fetch_source)
            )
            self._read_records(graph.references)
            return self._installed_record(
                recipe, graph.package(self.ref), with_sources=fetch_source
            )

    @staticmethod
    def _installed_record(
        recipe: StoredRecipe, package: NativePackage, *, with_sources: bool
    ) -> Record:
        if package.package_folder is None:
            raise CatalogError("Conan did not install the data package")
        source = None
        if with_sources:
            if package.folders.source_folder is None:
                raise CatalogError("Conan did not provide the SCM source folder")
            source = Path(package.folders.source_folder) / "source"
        return replace(
            recipe.record,
            source=source,
            files={
                name: Path(package.package_folder) / "artifacts" / name
                for name in recipe.record.files
            },
        )

    def _read_records(self, references: tuple[str, ...]) -> tuple[Record, ...]:
        return tuple(self._load(reference).record for reference in dict.fromkeys(references))

    def _load(self, reference: str) -> StoredRecipe:
        ref = from_conan_reference(reference, catalog=self._catalog)
        recipe = self._recipes.get(ref)
        if recipe is None:
            revisions = self._client.index(f"{ref}*#*").get(str(ref))
            recipe = self._download(ref, self._pin(ref, revisions))
        if "#" in reference and reference != recipe.reference:
            raise Conflict(f"Locked revision differs from published identity: {reference}")
        return recipe

    def _download(self, ref: Ref, reference: str) -> StoredRecipe:
        directory = self._client.download_recipe(reference)
        recipe = StoredRecipe.read(directory, ref, reference=reference)
        self._recipes[ref] = recipe
        return recipe

    @staticmethod
    def _pin(ref: Ref, revisions: RecipeRevisions | None) -> str:
        if revisions is None or not revisions.revisions:
            raise NotFound(ref)
        if len(revisions.revisions) != 1:
            raise Conflict(f"Multiple recipe revisions for {ref}")
        return f"{ref}#{next(iter(revisions.revisions))}"


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

    def publish(self) -> Record:
        record = self._publication_record()
        logical = to_conan_reference(record.ref)
        if logical in self._client.index(f"{logical}*#*"):
            raise Conflict("Record identity already published")
        recipe = LocalRecipe(record, self._requirements())
        with TemporaryDirectory(prefix="hwcatalog-conan-publish-") as temporary:
            directory = Path(temporary)
            recipe.write(directory)
            self._copy_files(directory, record)
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
        recipe = StoredRecipe.read(
            self._client.cache_path(pinned), self.record.ref, reference=pinned
        )
        self._client.upload(pinned)
        return recipe.record

    @staticmethod
    def _local_path(path: Path) -> Path:
        try:
            return path.expanduser().resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise InvalidPackage(f"Publication input does not exist: {path}") from exc

    @staticmethod
    def _copy_files(directory: Path, record: LocalRecord) -> None:
        for name, path in record.files.items():
            destination = directory / "artifacts" / name
            if not destination.resolve().is_relative_to((directory / "artifacts").resolve()):
                raise InvalidPackage("Artifact would escape the package directory")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
