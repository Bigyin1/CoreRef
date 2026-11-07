"""Project-owned editable Git dependencies, independent of Conan's file formats."""

from pathlib import Path

from .._internal.recipe import EditableRecipe
from .._internal.workspace import WorkspaceFile
from .catalog import Catalog
from .errors import Conflict, NotFound
from .records import InstalledRecord, Ref


class Workspace:
    """Register source-only packages in workspace.yaml, with Git copies beside the project."""

    def __init__(self, catalog: Catalog, path: str | Path = "workspace.yaml") -> None:
        self._file = WorkspaceFile(catalog, Path(path))

    @property
    def path(self) -> Path:
        return self._file.path

    @property
    def members(self) -> dict[Ref, Path]:
        """Return a detached snapshot of registered refs and working-copy paths."""
        return self._file.read()

    def add(self, ref: Ref, *, directory: str | Path | None = None) -> InstalledRecord:
        """Open one SCM package, or reuse its existing working copy without resetting edits."""
        entries = self._file.read()
        chosen = self._file.find(ref, entries)
        recipe = self._file.recipe(chosen)
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
        chosen = self._file.find(ref, entries)
        if chosen not in entries:
            raise NotFound(f"Package is not editable in this workspace: {ref}")
        entries.pop(chosen)
        self._file.save(entries)


__all__ = ["Workspace"]
