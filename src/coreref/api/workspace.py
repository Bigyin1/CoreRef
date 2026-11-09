"""Small wrappers around native workspace registration and repository opening."""

from __future__ import annotations

from pathlib import Path

from .._internal.recipe import LocalRecipe
from .catalog import Catalog
from .errors import InvalidPackage
from .records import LocalRecord, Ref


class Workspace:
    """Let Conan own workspace discovery, membership and editable selection."""

    def __init__(self, catalog: Catalog, path: str | Path = "conanws.yml") -> None:
        self._catalog = catalog
        self._path = Path(path).expanduser().resolve()
        if self._path.name != "conanws.yml":
            raise InvalidPackage("A workspace must be named conanws.yml")

    @property
    def path(self) -> Path:
        return self._path

    @property
    def members(self) -> dict[Ref, Path]:
        if not self.path.exists():
            return {}
        return {
            Ref(ref, catalog=self._catalog): path
            for ref, path in self._catalog._client.workspace_members(self.path.parent).items()
        }

    def init(self) -> None:
        self._catalog._client.run("workspace", "init", str(self.path.parent))

    def add(self, directory: str | Path) -> None:
        """Register a directory; prepare only conanfile.py for a standard deps.yml project."""
        from ..tools.deps import DepsFile

        source = Path(directory).expanduser().resolve()
        if not (source / "conanfile.py").exists():
            document = DepsFile(self._catalog, source / "deps.yml")
            LocalRecipe(
                document.record, tuple(str(ref) for ref in document.dependencies)
            ).write_editable(
                source,
                manifest="deps.yml",
            )
        self.init()
        self._catalog._client.run("workspace", "add", str(source), workspace=self.path.parent)

    def open(self, ref: Ref, *, directory: str | Path | None = None) -> Path:
        """Obtain the repository through Conan, add one recipe, then register the directory."""
        source = (
            Path(directory).expanduser().resolve()
            if directory
            else (self.path.parent.parent / f"{ref.package_name}-{ref.version}")
        )
        if not (source / "conanfile.py").exists():
            recipe = self._catalog._package(ref).recipe()
            if not recipe.has_source:
                raise InvalidPackage("The package has no repository source")
            self._catalog._client.open_sources(recipe.reference, source)
            record = LocalRecord(
                recipe.record.ref.without_revision(),
                recipe.record.payload,
                source=source,
                files={name: source / "artifacts" / name for name in recipe.record.file_names},
            )
            LocalRecipe(record, recipe.requirements).write_editable(source)
        self.add(source)
        return source

    def remove(self, directory: str | Path) -> None:
        self._catalog._client.run(
            "workspace",
            "remove",
            str(Path(directory).expanduser().resolve()),
            workspace=self.path.parent,
        )


__all__ = ["Workspace"]
