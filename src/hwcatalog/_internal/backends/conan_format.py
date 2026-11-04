"""Version-one recipe format shared by catalog publication and external CI."""

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from ...api.errors import InvalidPackage, InvalidPublication
from ...api.models.query import SearchQuery
from ...api.models.records import Record, Ref
from ..validation import normalize_record, normalize_ref

_TOKEN = re.compile(r"[a-z0-9_-]+\Z")
_VERSION = re.compile(r"[a-z0-9_][a-z0-9_+.-]{1,100}\Z")
_MAX_NAME_LENGTH = 101
_MAX_REFERENCE_LENGTH = 200


def to_conan_reference(ref: Ref) -> str:
    """Encode a reference, rejecting names that cannot be represented without loss."""
    ref = normalize_ref(ref)
    if not all(_TOKEN.fullmatch(value) for value in (ref.namespace, ref.name, ref.kind)):
        raise InvalidPublication(
            "Conan coordinates require lowercase tokens without dots or slashes"
        )
    name = f"{ref.namespace}.{ref.name}.{ref.kind}"
    value = f"{name}/{ref.version}"
    if (
        len(name) > _MAX_NAME_LENGTH
        or len(value) > _MAX_REFERENCE_LENGTH
        or not _VERSION.fullmatch(ref.version)
    ):
        raise InvalidPublication("Reference exceeds Conan name/version constraints")
    return value


def from_conan_reference(value: str) -> Ref:
    """Decode a recipe reference; revisions and timestamps are transport details."""
    try:
        name, version = value.split("#", 1)[0].split("/")
        namespace, component, kind = name.split(".")
        ref = Ref(component, version, kind, namespace)
        if to_conan_reference(ref) != value.split("#", 1)[0]:
            raise ValueError("Noncanonical coordinates")
        return ref
    except (ValueError, InvalidPublication) as exc:
        raise InvalidPackage(f"Not a catalog Conan reference: {value!r}") from exc


def search_pattern(query: SearchQuery) -> str | None:
    """Use wildcards only for unspecified fields, never for literal query text."""
    fields = (query.namespace, query.name, query.kind)
    if any(value is not None and not _TOKEN.fullmatch(value) for value in fields):
        return None
    if query.version is not None and not _VERSION.fullmatch(query.version):
        return None
    name = ".".join(value if value is not None else "*" for value in fields)
    return f"{name}/{query.version if query.version is not None else '*'}#*"


class RecipeMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal[1] = 1
    requires: tuple[str, ...] = ()


class Lockfile(BaseModel):
    version: Literal["0.5"]
    requires: tuple[str, ...]
    build_requires: tuple[str, ...] = ()
    python_requires: tuple[str, ...] = ()
    config_requires: tuple[str, ...] = ()


def _recipe_text(ref: Ref, requirements: tuple[str, ...]) -> str:
    name, version = to_conan_reference(ref).split("/")
    return (
        "from conan import ConanFile\n\n\n"
        "class CatalogRecord(ConanFile):\n"
        f"    name = {name!r}\n"
        f"    version = {version!r}\n"
        '    package_type = "header-library"\n'
        '    exports = "record.json", "hwcatalog.json", "conan.lock"\n'
        f"    requires = {requirements!r}\n"
    )


def write_recipe(directory: Path, record: Record, dependencies: tuple[Ref, ...] = ()) -> None:
    """Prepare a canonical recipe; CI must create conan.lock before exporting it."""
    record = normalize_record(record)
    requirements = tuple(sorted({to_conan_reference(ref) for ref in dependencies}))
    metadata = RecipeMetadata(requires=requirements)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "conanfile.py").write_text(_recipe_text(record.ref, requirements))
    (directory / "record.json").write_text(record.to_json())
    (directory / "hwcatalog.json").write_text(metadata.model_dump_json())


def read_recipe(directory: Path, expected: Ref) -> tuple[Record, RecipeMetadata, Lockfile]:
    """Validate the exported format before allowing Conan to evaluate the recipe."""
    try:
        record = normalize_record(Record.from_json((directory / "record.json").read_text()))
        metadata = RecipeMetadata.model_validate_json((directory / "hwcatalog.json").read_text())
        lockfile = Lockfile.model_validate_json((directory / "conan.lock").read_text())
        if record.ref != expected:
            raise ValueError("Record coordinates disagree with the recipe")
        for dependency in metadata.requires:
            if to_conan_reference(from_conan_reference(dependency)) != dependency:
                raise ValueError("Requirements must use exact versions without revisions")
        if (directory / "conanfile.py").read_text() != _recipe_text(record.ref, metadata.requires):
            raise ValueError("Only canonical version-one catalog recipes are supported")
        _validate_lockfile(lockfile, expected, metadata)
        return record, metadata, lockfile
    except (OSError, ValueError, TypeError, KeyError, ValidationError, InvalidPublication) as exc:
        raise InvalidPackage(f"Invalid catalog recipe for {expected.identity}: {exc}") from exc


def _validate_lockfile(lockfile: Lockfile, root: Ref, metadata: RecipeMetadata) -> None:
    if lockfile.build_requires or lockfile.python_requires or lockfile.config_requires:
        raise ValueError("Only host requirements are supported")
    locked = set()
    for value in lockfile.requires:
        logical, separator, revision = value.partition("#")
        if not separator or not re.fullmatch(r"[a-f0-9]{32,40}(?:%[0-9.]+)?", revision):
            raise ValueError("Every locked dependency must include a recipe revision")
        if from_conan_reference(logical) == root:
            raise ValueError("Lockfile cannot contain its own root")
        locked.add(logical)
    if not set(metadata.requires).issubset(locked):
        raise ValueError("Lockfile does not cover all direct requirements")


def lock_references(lockfile: Lockfile) -> frozenset[str]:
    """Pinned recipe references without Conan's timestamp suffix."""
    return frozenset(value.split("%", 1)[0] for value in lockfile.requires)
