"""Project-owned editable Git dependencies, independent of Conan's file formats."""

from collections.abc import Iterable
from pathlib import Path

from .._internal.recipe import EditableRecipe, StoredRecipe
from .._internal.workspace import WorkspaceFile
from .catalog import Catalog
from .errors import Conflict, InvalidPackage, NotFound
from .records import InstalledRecord, Record, Ref


class Workspace:
    """Register source-only packages in workspace.yaml, with Git copies beside the project."""

    def __init__(self, catalog: Catalog, path: str | Path = "workspace.yaml") -> None:
        self._file = WorkspaceFile(catalog, Path(path))

    @property
    def path(self) -> Path:
        return self._file.path

    def add(self, ref: Ref, *, directory: str | Path | None = None) -> InstalledRecord:
        """Open one SCM package, or reuse its existing working copy without resetting edits."""
        entries = self._file.read()
        chosen = self._match(ref, {item.without_revision(): item for item in entries})
        recipe = self._recipe(chosen)
        parent = (
            Path(directory).expanduser().resolve()
            if directory is not None
            else self.path.parent.parent
        )
        source = entries.get(chosen, parent / f"{ref.package_name}-{ref.version}")
        editable = EditableRecipe(recipe, source)
        if (
            chosen not in entries
            and source.exists()
            and not self._file.owns_source(recipe.record.ref, source)
        ):
            raise Conflict(f"Editable destination already exists: {source}")
        if not source.exists():
            self._file.catalog._client.open_sources(recipe.reference, source)
        editable.write(self._file.recipe_directory(recipe.record.ref))
        entries[recipe.record.ref] = source
        self._file.save(entries)
        return editable.record

    def remove(self, ref: Ref) -> None:
        """Unregister one package, keeping its Git working copy and local changes."""
        entries = self._file.read()
        chosen = self._match(ref, {item.without_revision(): item for item in entries})
        if chosen not in entries:
            raise NotFound(f"Package is not editable in this workspace: {ref}")
        entries.pop(chosen)
        self._file.save(entries)

    def _recipe(self, ref: Ref) -> StoredRecipe:
        directory = self._file.recipe_directory(ref)
        if ref.revision is not None and (directory / "record.json").is_file():
            return StoredRecipe(directory, ref, reference=str(ref))
        return self._file.catalog._package(ref).recipe()

    def _match(self, ref: Ref, entries: dict[Ref, Ref]) -> Ref:
        selected = entries.get(ref.without_revision(), ref)
        if ref.revision is not None and selected != ref:
            raise Conflict(f"Workspace uses {selected}; requested {ref}")
        return selected

    def _pin(self, refs: tuple[Ref, ...]) -> tuple[Ref, ...]:
        entries = {ref.without_revision(): ref for ref in self._file.read()}
        return tuple(self._match(ref, entries) for ref in refs)

    def _select(self, records: Iterable[Record]) -> dict[Ref, InstalledRecord]:
        entries = {
            ref.without_revision(): (ref, source) for ref, source in self._file.read().items()
        }
        selected = {}
        for record in records:
            member = entries.get(record.ref.without_revision())
            if member is not None:
                ref, source = member
                if ref != record.ref:
                    raise Conflict(f"Workspace uses {ref}; graph requires {record.ref}")
                if not record.has_source or record.file_names or not source.is_dir():
                    raise InvalidPackage(
                        "Workspace requires an existing SCM source-only working copy"
                    )
                selected[ref] = InstalledRecord(record, source=source)
        return selected


__all__ = ["Workspace"]
