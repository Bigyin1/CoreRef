"""The project workspace manifest and its derived Conan index."""

import os
from pathlib import Path

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..api.catalog import Catalog
from ..api.errors import InvalidPackage
from ..api.records import Ref
from .recipe import _RECIPE_ENV
from .references import from_conan_reference


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    packages: dict[str, str] = Field(default_factory=dict)


class WorkspaceFile:
    """Read selected refs and source paths; keep the Conan representation private."""

    def __init__(self, catalog: Catalog, path: Path) -> None:
        self.catalog = catalog
        self.path = path.expanduser().resolve()
        self.native_directory = self.path.parent / ".hwcatalog" / "workspaces" / self.path.name

    def read(self) -> dict[Ref, Path]:
        try:
            value = yaml.safe_load(self.path.read_text()) if self.path.exists() else {}
            manifest = _Manifest.model_validate(value or {})
            entries = {}
            logical = set()
            for reference, source in manifest.packages.items():
                ref = from_conan_reference(reference, catalog=self.catalog)
                if ref.revision is None or ref.without_revision() in logical:
                    raise InvalidPackage(
                        "Workspace refs must have distinct coordinates and a base revision"
                    )
                logical.add(ref.without_revision())
                entries[ref] = (self.path.parent / source).resolve()
            return entries
        except (OSError, ValidationError, yaml.YAMLError) as error:
            raise InvalidPackage(f"Invalid workspace manifest {self.path}: {error}") from error

    def recipe_directory(self, ref: Ref) -> Path:
        return (
            self.native_directory
            / "recipes"
            / f"{ref.package_name}-{ref.version}"
            / str(ref.revision)
        )

    def owns_source(self, ref: Ref, source: Path) -> bool:
        marker = self.recipe_directory(ref) / "source-path.txt"
        return marker.is_file() and (marker.parent / marker.read_text()).resolve() == source

    def save(self, entries: dict[Ref, Path]) -> None:
        packages = {
            str(ref): os.path.relpath(source, self.path.parent)
            for ref, source in sorted(entries.items())
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(yaml.safe_dump({"packages": packages}, sort_keys=False))
        self.native_directory.mkdir(parents=True, exist_ok=True)
        ignore = self.path.parent / ".hwcatalog" / ".gitignore"
        if not ignore.exists():
            ignore.write_text("*\n")
        native = [
            {
                "ref": str(ref.without_revision()),
                "path": os.path.relpath(self.recipe_directory(ref), self.native_directory),
            }
            for ref in sorted(entries)
        ]
        template = _RECIPE_ENV.get_template("workspace.yml.j2")
        (self.native_directory / "conanws.yml").write_text(template.render(packages=native))
