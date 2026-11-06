"""Prepared local recipes and validated recipes obtained from Conan."""

import json
import shutil
from dataclasses import replace
from pathlib import Path

import yaml  # type: ignore[import-untyped]  # Validated at this boundary.
from edify import RegexBuilder
from jinja2 import Environment, PackageLoader, StrictUndefined
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from ..api.errors import CatalogError, InvalidPackage
from ..api.records import LocalRecord, Record, Ref
from .references import from_conan_reference, to_conan_reference
from .types import NativePackage

_RECIPE_ENV = Environment(
    loader=PackageLoader("hwcatalog._internal"),
    autoescape=False,  # noqa: S701 - Python literals use repr escaping.
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_RECIPE_ENV.filters["pyrepr"] = repr
_RECIPE_TEMPLATE = _RECIPE_ENV.get_template("conanfile.py.j2")
_CONANDATA_TEMPLATE = _RECIPE_ENV.get_template("conandata.yml.j2")
_HEX_DIGIT = RegexBuilder().any_of().range("a", "f").range("0", "9").end()
_COMMIT = (
    RegexBuilder()
    .any_of()
    .exactly(40)
    .use(_HEX_DIGIT)
    .exactly(64)
    .use(_HEX_DIGIT)
    .end()
    .to_regex()
    .compiled
)


def _recipe_text(ref: Ref) -> str:
    name, version = to_conan_reference(ref).split("/")
    return _RECIPE_TEMPLATE.render(name=name, version=version)


class _SCM(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    url: str
    commit: str
    requested_ref: str = "HEAD"

    @field_validator("commit")
    @classmethod
    def validate_commit(cls, value: str) -> str:
        if not _COMMIT.fullmatch(value):
            raise ValueError("Git commit must be a full lowercase SHA-1 or SHA-256")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if not value.strip() or value.startswith("-"):
            raise ValueError("Git URL must be nonempty and must not start with a dash")
        if any(char in value for char in ('"', "`", "$", "\n", "\r", "\0")):
            raise ValueError("Git URL contains characters unsafe for Conan SCM helpers")
        return value


class _ConanData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    requires: tuple[str, ...] = Field(default=(), strict=False)
    scm: _SCM | None = None


class LocalRecipe:
    """A local package description ready to be written into a staging directory."""

    def __init__(self, record: LocalRecord, requirements: tuple[str, ...] = ()) -> None:
        self.record = record
        self.requirements = tuple(sorted(set(requirements)))

    def write(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "conanfile.py").write_text(_recipe_text(self.record.ref))
        (directory / "record.json").write_text(self.record.to_json())
        (directory / "conandata.yml").write_text(
            _CONANDATA_TEMPLATE.render(requirements=self.requirements)
        )
        if self.record.source is not None:
            (directory / "source-path.txt").write_text(str(self.record.source))

    def copy_files(self, directory: Path) -> None:
        """Stage this publication's files alongside the prepared recipe."""
        for name, path in self.record.files.items():
            destination = directory / "artifacts" / name
            if not destination.resolve().is_relative_to((directory / "artifacts").resolve()):
                raise InvalidPackage("Artifact would escape the package directory")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)


class StoredRecipe:
    """Read and validate one recipe revision before Conan evaluates its code."""

    def __init__(self, directory: Path, expected: Ref, *, reference: str) -> None:
        self.reference = reference
        self.directory = directory
        try:
            self.record = self._read_record(expected)
            self._data = self._read_data()
        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            ValidationError,
            InvalidPackage,
            yaml.YAMLError,
        ) as exc:
            raise InvalidPackage(f"Invalid catalog recipe for {expected}: {exc}") from exc

    @property
    def requirements(self) -> tuple[str, ...]:
        return self._data.requires

    @property
    def has_source(self) -> bool:
        return self._data.scm is not None

    def _read_record(self, expected: Ref) -> Record:
        value = json.loads((self.directory / "record.json").read_text())
        if not isinstance(value, dict):
            raise ValueError("Record must be a JSON object")
        if value.get("format") != LocalRecord._format:
            raise ValueError("Unsupported record format")
        if any(name in value for name in ("source", "source_path", "scm")):
            raise ValueError("Git source must be stored in Conan SCM metadata")
        record = Record(
            Ref(**value["ref"], catalog=expected._catalog),
            value["payload"],
            files=self._read_files(value.get("files", [])),
        )
        if record.ref != expected:
            raise ValueError("Record coordinates disagree with the recipe")
        return record

    def _read_files(self, names: object) -> dict[str, Path]:
        if not isinstance(names, list):
            raise ValueError("Package files must be a list of stored names")
        folder = self.directory / "artifacts"
        files = {name: folder / name for name in names}
        if any(not path.resolve().is_relative_to(folder.resolve()) for path in files.values()):
            raise ValueError("Artifact would escape the package directory")
        return files

    def installed(self, package: NativePackage, *, with_sources: bool) -> Record:
        """Attach Conan cache paths to this recipe's already validated record."""
        files: dict[str, Path] = {}
        if self.record.files:
            if package.package_folder is None:
                raise CatalogError("Conan did not install the data package")
            folder = Path(package.package_folder) / "artifacts"
            files = {name: folder / name for name in self.record.files}
        source = None
        if with_sources:
            if package.folders.source_folder is None:
                raise CatalogError("Conan did not provide the SCM source folder")
            source = Path(package.folders.source_folder) / "source"
        return replace(self.record, source=source, files=files)

    def _read_data(self) -> _ConanData:
        if (self.directory / "conanfile.py").read_text() != _recipe_text(self.record.ref):
            raise ValueError("Only canonical catalog recipes are supported")
        data = _ConanData.model_validate(
            yaml.safe_load((self.directory / "conandata.yml").read_text())
        )
        for dependency in data.requires:
            from_conan_reference(dependency, catalog=self.record.ref._catalog)
        return data
