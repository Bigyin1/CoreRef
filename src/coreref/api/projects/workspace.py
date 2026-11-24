"""Register and open editable checkouts using Conan workspace discovery.

Conan stores membership in its workspace files and detects the
workspace from the working directory of subsequent commands.
"""

from __future__ import annotations

from pathlib import Path

from ..._internal.recipe import LocalRecipe
from ..errors import InvalidPackage
from ..packages.catalog import Catalog
from ..packages.records import Dependency, LocalRecord, Ref


class Workspace:
    """Manage the native Conan workspace associated with a project directory.

    Register an existing checkout with ``add``, or obtain the Git source
    of a catalog package with ``open``. Resolve graphs from a directory where
    Conan can discover the workspace to use those editables.
    """

    def __init__(self, catalog: Catalog, path: str | Path = "conanws.yml") -> None:
        """Associate a catalog with a path ending in ``conanws.yml``."""
        self._catalog = catalog
        self._path = Path(path).expanduser().resolve()
        if self._path.name != "conanws.yml":
            raise InvalidPackage("A workspace must be named conanws.yml")

    @property
    def path(self) -> Path:
        """Return the absolute path to Conan's workspace file."""
        return self._path

    @property
    def members(self) -> dict[Ref, Path]:
        """Return registered package references and their checkout paths.

        An uninitialized workspace reports an empty mapping.
        """
        if not self.path.exists():
            return {}
        return {
            Ref(ref): path
            for ref, path in self._catalog._client.workspace_members(self.path.parent).items()
        }

    def init(self) -> None:
        """Initialize the workspace through Conan in the workspace directory."""
        self._catalog._client.run("workspace", "init", str(self.path.parent))

    def add(
        self,
        directory: str | Path,
        *,
        record: LocalRecord | None = None,
        dependencies: tuple[Dependency, ...] = (),
    ) -> None:
        """Register an existing checkout as an editable dependency.

        Supply a ``LocalRecord`` and its requirements to generate a
        ``conanfile.py`` if one is missing. An existing recipe is reused.
        ``dependencies`` requires a ``record``. The checkout may already
        exist outside the workspace root.
        """
        source = Path(directory).expanduser().resolve()
        if record is not None:
            LocalRecipe(record, tuple(str(ref) for ref in dependencies)).write_editable(source)
        elif dependencies:
            raise InvalidPackage("Dependencies require a local record")
        self.init()
        self._catalog._client.run("workspace", "add", str(source), workspace=self.path.parent)

    def open(self, ref: Ref, *, directory: str | Path | None = None) -> Path:
        """Obtain a package's Git source and register it as editable.

        By default, use a sibling of the workspace directory named after
        the package and version. A directory with an existing recipe is
        registered directly. Otherwise, fetch the package source through
        Conan and write a recipe for the checkout. Return the checkout path.
        """
        source = (
            Path(directory).expanduser().resolve()
            if directory
            else (self.path.parent.parent / f"{ref.package_name}-{ref.version}")
        )
        if not (source / "conanfile.py").exists():
            recipe = self._catalog._package(ref).recipe()
            if not recipe.has_source:
                raise InvalidPackage("The package has no repository source")
            requirements = self._catalog._client.recipe_requirements(recipe.directory)
            self._catalog._client.open_sources(recipe.reference, source)
            record = LocalRecord(
                recipe.record.ref.without_revision(),
                recipe.record.payload,
                source=source,
                files={name: source / "artifacts" / name for name in recipe.record.file_names},
            )
            LocalRecipe(record, requirements).write_editable(source)
        self.add(source)
        return source

    def remove(self, directory: str | Path) -> None:
        """Unregister a checkout directory through Conan."""
        self._catalog._client.run(
            "workspace",
            "remove",
            str(Path(directory).expanduser().resolve()),
            workspace=self.path.parent,
        )


__all__ = ["Workspace"]
