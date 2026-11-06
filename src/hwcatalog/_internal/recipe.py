"""Prepared local recipes and validated recipes obtained from Conan."""

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, Self

import yaml  # type: ignore[import-untyped]  # Validated at this boundary.
from edify import RegexBuilder
from jinja2 import Environment, PackageLoader, StrictUndefined
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

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


class RecipeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal[5]
    has_source: bool = False
    requires: tuple[str, ...] = ()


def _recipe_text(ref: Ref, requirements: tuple[str, ...]) -> str:
    name, version = to_conan_reference(ref).split("/")
    return _RECIPE_TEMPLATE.render(name=name, version=version, requirements=requirements)


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
    scm: _SCM


class LocalRecipe:
    """A local package description ready to be written into a staging directory."""

    def __init__(self, record: LocalRecord, requirements: tuple[str, ...] = ()) -> None:
        self.record = record
        self.metadata = RecipeMetadata(
            format=5, requires=tuple(sorted(set(requirements))), has_source=bool(record.source)
        )

    def write(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "conanfile.py").write_text(
            _recipe_text(self.record.ref, self.metadata.requires)
        )
        (directory / "record.json").write_text(self.record.to_json())
        (directory / "hwcatalog.json").write_text(self.metadata.model_dump_json())
        if self.record.source is not None:
            (directory / "source-path.txt").write_text(str(self.record.source))


@dataclass(frozen=True)
class StoredRecipe:
    """One validated recipe revision and its already downloaded metadata."""

    reference: str
    directory: Path
    record: Record
    metadata: RecipeMetadata

    @classmethod
    def read(cls, directory: Path, expected: Ref, *, reference: str) -> Self:
        """Validate metadata and canonical code before Conan evaluates the recipe."""
        try:
            recipe = cls(
                reference,
                directory,
                cls._read_record(directory, expected),
                RecipeMetadata.model_validate_json((directory / "hwcatalog.json").read_text()),
            )
            recipe._validate()
            return recipe
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

    @staticmethod
    def _read_record(directory: Path, expected: Ref) -> Record:
        value = json.loads((directory / "record.json").read_text())
        if not isinstance(value, dict):
            raise ValueError("Record must be a JSON object")
        if any(name in value for name in ("source", "source_path", "scm")):
            raise ValueError("Git source must be stored in Conan SCM metadata")
        names = value.get("files", [])
        if not isinstance(names, list):
            raise ValueError("Package files must be a list of stored names")
        record = Record(
            Ref(**value["ref"], catalog=expected._catalog),
            value["payload"],
            files={name: directory / "artifacts" / name for name in names},
        )
        if any(
            not path.resolve().is_relative_to((directory / "artifacts").resolve())
            for path in record.files.values()
        ):
            raise ValueError("Artifact would escape the package directory")
        if record.ref != expected:
            raise ValueError("Record coordinates disagree with the recipe")
        return record

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

    def _validate(self) -> None:
        if self.metadata.has_source:
            _ConanData.model_validate(
                yaml.safe_load((self.directory / "conandata.yml").read_text())
            )
        for dependency in self.metadata.requires:
            ref = from_conan_reference(dependency, catalog=self.record.ref._catalog)
            if to_conan_reference(ref) != dependency.split("#", 1)[0]:
                raise ValueError("Requirements must use canonical exact references")
        expected = _recipe_text(self.record.ref, self.metadata.requires)
        if (self.directory / "conanfile.py").read_text() != expected:
            raise ValueError("Only canonical catalog recipes are supported")
