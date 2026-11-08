"""Operations on one local package or one published package reference."""

import json
from dataclasses import replace
from functools import cached_property
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from frozendict import deepfreeze, frozendict

from ..api.errors import Conflict, InvalidPackage, NotFound
from ..api.query import SearchQuery
from ..api.records import InstalledRecord, LocalRecord, Record, Ref
from .graph import GraphData, ResolvedConanGraph
from .recipe import ConsumerRecipe, EditableRecipe, LocalRecipe, StoredRecipe
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
        self._resolved: dict[Ref, Ref] = {}

    def recipe(self) -> StoredRecipe:
        return self._load(str(self.ref))

    def record_at(self, reference: str, *, directory: Path) -> Record:
        """Read an exact recipe already located by Conan during graph resolution."""
        return self._load(reference, directory=directory).record

    def info(self) -> Record:
        return self.recipe().record

    def get(self) -> InstalledRecord:
        return self._get(with_sources=False)

    def get_with_sources(self) -> InstalledRecord:
        return self._get(with_sources=True)

    def open(self, source: Path) -> InstalledRecord:
        """Prepare the editable recipe once, preserving an existing working copy."""
        if source.exists():
            if not (source / "conandata.yml").is_file() or not (source / "record.json").is_file():
                raise Conflict(f"Editable destination already exists: {source}")
            ref = self._resolve_ref(self.ref)
            recipe = StoredRecipe(source, ref, reference=str(ref))
            if recipe.data.get("coreref_revision") != ref.revision:
                raise Conflict(f"Editable base revision differs from {ref}")
            return EditableRecipe(recipe, source).record
        recipe = self.recipe()
        editable = EditableRecipe(recipe, source)
        self._client.open_sources(recipe.reference, source)
        editable.write()
        return editable.record

    def resolved_graph(self, lockfile: Path | None, workspace: Path | None = None) -> PackageGraph:
        """Resolve the stored root itself, retaining its exact revision and metadata."""
        with TemporaryDirectory(prefix="coreref-resolve-") as temporary:
            directory = Path(temporary)
            ConsumerRecipe((str(self.ref),)).write(directory)
            native = self._client.graph(
                directory,
                lockfile=lockfile,
                **({"workspace": workspace} if workspace is not None else {}),
                update=(
                    self.ref.revision is None
                    and lockfile is None
                    and self._client.remote is not None
                ),
            )
        graph = ResolvedConanGraph(native)
        reference = graph.direct_references[0]
        ref = from_conan_reference(reference, catalog=self._catalog)
        record = self.record_at(reference, directory=graph.recipe_directory(ref))
        # Later fetches must reuse Conan's choice, including an older locked root.
        self._resolved[self.ref.without_revision()] = record.ref
        return PackageGraph(record, graph, packages={record.ref: self}, workspace=workspace)

    def dependencies(self, query: SearchQuery) -> tuple[Record, ...]:
        return tuple(self._load(str(ref)).record for ref in self.dependency_refs(query))

    def dependency_refs(self, query: SearchQuery) -> tuple[Ref, ...]:
        refs = set()
        logical_query = replace(query, revision=None)
        for reference in dict.fromkeys(self.recipe().requirements):
            ref = from_conan_reference(reference, catalog=self._catalog)
            # Floating requirements need Conan's latest choice before filtering RREV.
            prefilter = logical_query if ref.revision is None else query
            if not prefilter.matches_ref(ref):
                continue
            resolved = self._resolve_ref(ref)
            if query.matches_ref(resolved):
                refs.add(resolved)
        return tuple(sorted(refs))

    def _get(self, *, with_sources: bool) -> InstalledRecord:
        recipe = self.recipe()
        fetch_source = with_sources and recipe.has_source
        if not recipe.record.file_names and not fetch_source:
            return InstalledRecord(recipe.record)
        with TemporaryDirectory(prefix="coreref-install-") as temporary:
            graph = ResolvedConanGraph(
                self._client.install(recipe.reference, Path(temporary), with_sources=fetch_source)
            )
            self._read_records(graph)
            return recipe.installed(graph.package(recipe.record.ref), with_sources=fetch_source)

    def _read_records(self, graph: ResolvedConanGraph) -> None:
        for reference in graph.references:
            ref = from_conan_reference(reference, catalog=self._catalog)
            self.record_at(reference, directory=graph.recipe_directory(ref))

    def _resolve_ref(self, ref: Ref) -> Ref:
        if ref.revision is not None:
            return ref
        if ref not in self._resolved:
            revisions = self._client.index(f"{ref}*#latest").get(str(ref))
            if revisions is None or not revisions.revisions:
                raise NotFound(ref)
            revision = next(iter(revisions.revisions))
            self._resolved[ref] = from_conan_reference(f"{ref}#{revision}", catalog=self._catalog)
        return self._resolved[ref]

    def _load(self, reference: str, *, directory: Path | None = None) -> StoredRecipe:
        ref = self._resolve_ref(from_conan_reference(reference, catalog=self._catalog))
        if ref not in self._recipes:
            if directory is None:
                directory = self._client.download_recipe(str(ref))
            self._recipes[ref] = StoredRecipe(directory, ref, reference=str(ref))
        return self._recipes[ref]


class PackageGraph:
    """A resolved local or published root with shared recipe and installation caches."""

    def __init__(
        self,
        record: LocalRecord | Record,
        graph: ResolvedConanGraph,
        *,
        packages: dict[Ref, StoredPackage],
        workspace: Path | None = None,
    ) -> None:
        self.record = record
        self.graph = graph
        self._catalog = record.ref._catalog
        self._client = self._catalog._client
        self._packages = dict(packages)
        self.workspace = workspace
        self._fetched: dict[Ref, InstalledRecord] = {}

        self.result: GraphData = self.graph.result(
            self.record, self._read_records(self.graph.references)
        )
        self.editables = tuple(
            EditableRecipe(self._packages[ref].recipe(), self.graph.recipe_directory(ref)).record
            for ref in self._packages
            if str(ref) in self.graph.editable_references
        )
        self._fetched.update((record.ref, record) for record in self.editables)

    def create_lockfile(self, path: str | Path) -> Path:
        output = Path(path).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        self._client.save_lockfile(self.graph.native, output)
        return output

    def get(self, refs: tuple[Ref, ...]) -> tuple[InstalledRecord, ...]:
        return self._get(refs, with_sources=False)

    def get_with_sources(self, refs: tuple[Ref, ...]) -> tuple[InstalledRecord, ...]:
        return self._get(refs, with_sources=True)

    def _get(self, refs: tuple[Ref, ...], *, with_sources: bool) -> tuple[InstalledRecord, ...]:
        selected = tuple(dict.fromkeys(self.resolve_ref(ref) for ref in refs))
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
        return with_sources and not record.sources_ready

    def _install(
        self, recipes: tuple[StoredRecipe, ...], *, with_sources: bool
    ) -> tuple[InstalledRecord, ...]:
        selected = {recipe.record.ref for recipe in recipes if with_sources and recipe.has_source}
        sources = {str(ref): ref in selected for ref in self._packages}
        with TemporaryDirectory(prefix="coreref-batch-") as temporary:
            directory = Path(temporary)
            ConsumerRecipe(tuple(recipe.reference for recipe in recipes)).write(directory)
            self._client.save_lockfile(self.graph.native, directory / "conan.lock")
            installed = ResolvedConanGraph(
                self._client.install_project(
                    directory,
                    sources,
                    **({"workspace": self.workspace} if self.workspace is not None else {}),
                )
            )
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

    def _read_records(self, references: tuple[str, ...]) -> tuple[Record, ...]:
        records = []
        for reference in dict.fromkeys(references):
            ref = from_conan_reference(reference, catalog=self._catalog)
            if ref not in self._packages:
                self._packages[ref] = StoredPackage(ref)
            records.append(
                self._packages[ref].record_at(reference, directory=self.graph.recipe_directory(ref))
            )
        return tuple(records)


class LocalPackage:
    """A detached local description and dependencies for one catalog operation."""

    def __init__(
        self,
        catalog: Catalog,
        record: LocalRecord,
        *,
        dependencies: tuple[Ref, ...],
        lockfile: Path | None,
        workspace: Path | None = None,
    ) -> None:
        self._catalog = catalog
        self._client = catalog._client
        self.record = self._snapshot(record)
        self.dependencies = self._references(dependencies)
        self.lockfile = lockfile
        self.workspace = workspace

    @cached_property
    def graph(self) -> ResolvedConanGraph:
        recipe = LocalRecipe(self.record, tuple(str(ref) for ref in self.dependencies))
        with TemporaryDirectory(prefix="coreref-resolve-") as temporary:
            directory = Path(temporary)
            recipe.write(directory)
            update = (
                self.lockfile is None
                and self._client.remote is not None
                and any(ref.revision is None for ref in self.dependencies)
            )
            if update:
                native = self._client.graph(
                    directory,
                    lockfile=self.lockfile,
                    update=True,
                    **({"workspace": self.workspace} if self.workspace is not None else {}),
                )
            else:
                native = self._client.graph(
                    directory,
                    lockfile=self.lockfile,
                    update=False,
                    **({"workspace": self.workspace} if self.workspace is not None else {}),
                )
            return ResolvedConanGraph(native)

    @cached_property
    def resolved(self) -> PackageGraph:
        return PackageGraph(self.record, self.graph, packages={}, workspace=self.workspace)

    def publish(self, *, resolved: ResolvedConanGraph | None = None) -> InstalledRecord:
        record = self._publication_record()
        if resolved is None and self.dependencies:
            resolved = self.resolved.graph
        requirements = resolved.direct_references if resolved is not None else ()
        recipe = LocalRecipe(record, requirements)
        with TemporaryDirectory(prefix="coreref-publish-") as temporary:
            directory = Path(temporary)
            recipe.write(directory)
            recipe.copy_files(directory)
            lockfile = self.lockfile
            if resolved is not None:
                lockfile = directory / "conan.lock"
                self._client.save_lockfile(resolved.native, lockfile)
            return self._export(directory, lockfile=lockfile)

    def _snapshot(self, record: LocalRecord) -> LocalRecord:
        if not isinstance(record, LocalRecord):
            raise InvalidPackage("Expected one LocalRecord")
        if record.ref.revision is not None:
            raise InvalidPackage("A local package root must not specify a revision")
        to_conan_reference(record.ref)
        source = record.source.expanduser().absolute() if record.source is not None else None
        return LocalRecord(
            Ref.from_fields(**record.ref.to_dict(), catalog=self._catalog),
            deepfreeze(json.loads(record.to_json())["payload"]),
            source,
            frozendict(record.files),
        )

    def _references(self, dependencies: tuple[Ref, ...]) -> tuple[Ref, ...]:
        if any(not isinstance(ref, Ref) for ref in dependencies):
            raise InvalidPackage("Dependencies must be Ref values")
        refs = tuple(
            Ref.from_fields(**ref.to_dict(), catalog=self._catalog) for ref in dependencies
        )
        logical = tuple(ref.without_revision() for ref in refs)
        if len(logical) != len(set(logical)):
            raise InvalidPackage("Duplicate dependency")
        if self.record.ref in logical:
            raise InvalidPackage("A package cannot depend on itself")
        return refs

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

    def _export(self, directory: Path, *, lockfile: Path | None) -> InstalledRecord:
        native = self._client.export_package(directory, lockfile=lockfile)
        reference = native.root.ref
        if reference is None:
            raise InvalidPackage("Conan did not return the exported recipe reference")
        pinned = reference.repr_notime()
        recipe = StoredRecipe(self._client.cache_path(pinned), self.record.ref, reference=pinned)
        installed = recipe.installed(native.root.conanfile, with_sources=False)
        self._client.upload(pinned)
        return installed

    @staticmethod
    def _local_path(path: Path) -> Path:
        try:
            return path.expanduser().resolve(strict=True)
        except (OSError, ValueError) as exc:
            raise InvalidPackage(f"Publication input does not exist: {path}") from exc
