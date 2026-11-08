"""Conan workspaces with editable Git dependencies beside the project."""

from pathlib import Path

from .catalog import Catalog
from .errors import Conflict, InvalidPackage, NotFound
from .records import InstalledRecord, Ref


class Workspace:
    """Manage conanws.yml through Conan and keep editable recipes in their Git copies."""

    def __init__(self, catalog: Catalog, path: str | Path = "conanws.yml") -> None:
        self._catalog = catalog
        self._path = Path(path).expanduser().resolve()
        if self._path.name != "conanws.yml":
            raise InvalidPackage("A Conan workspace must be named conanws.yml")

    @property
    def path(self) -> Path:
        return self._path

    @property
    def members(self) -> dict[Ref, Path]:
        """Read registered packages through Conan's workspace API."""
        if not self.path.exists():
            return {}
        return {
            Ref(reference, catalog=self._catalog): source
            for reference, source in self._catalog._client.workspace_members(
                self.path.parent
            ).items()
        }

    def add(self, ref: Ref, *, directory: str | Path | None = None) -> InstalledRecord:
        """Open a Git copy once and register its generated recipe with Conan."""
        members = self.members
        chosen = next((r for r in members if r.without_revision() == ref.without_revision()), ref)
        if ref.revision is not None and chosen != ref:
            raise Conflict(f"Workspace uses {chosen}; requested {ref}")
        if chosen in members:
            return self._catalog._package(chosen).open(members[chosen])
        parent = Path(directory).expanduser().resolve() if directory else self.path.parent.parent
        source = parent / f"{ref.package_name}-{ref.version}"
        record = self._catalog._package(ref).open(source)
        if not self.path.exists():
            self._catalog._client.run("workspace", "init", str(self.path.parent))
        self._catalog._client.run("workspace", "add", str(source), workspace=self.path.parent)
        return record

    def remove(self, ref: Ref) -> None:
        """Unregister through Conan, keeping the Git copy and its generated files."""
        members = self.members
        chosen = next((r for r in members if r.without_revision() == ref.without_revision()), ref)
        if chosen not in members:
            raise NotFound(f"Package is not editable in this workspace: {ref}")
        if ref.revision is not None and chosen != ref:
            raise Conflict(f"Workspace uses {chosen}; requested {ref}")
        self._catalog._client.run(
            "workspace", "remove", str(members[chosen]), workspace=self.path.parent
        )


__all__ = ["Workspace"]
