"""Canonical catalog recipe metadata; lockfile contents belong to Conan."""

import re
from dataclasses import replace
from pathlib import Path
from typing import Literal

import yaml  # type: ignore[import-untyped]  # Validated by models at this boundary.
from jinja2 import Environment, PackageLoader, StrictUndefined
from pydantic import BaseModel, ConfigDict, ValidationError

from ...api.errors import InvalidPackage
from ...api.protocols import RefCatalog
from ...api.query import SearchQuery
from ...api.records import LocalRecord, Record, Ref
from ..validation import normalize_record, normalize_ref

_TOKEN = re.compile(r"[a-z0-9_-]+\Z")
_VERSION = re.compile(r"[a-z0-9_][a-z0-9_+.-]{1,100}\Z")
_MAX_NAME_LENGTH = 101
_MAX_REFERENCE_LENGTH = 200

_RECIPE_ENV = Environment(
    loader=PackageLoader("hwcatalog._internal.conan"),
    autoescape=False,  # noqa: S701 - Python literals use repr escaping.
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
)
_RECIPE_ENV.filters["pyrepr"] = repr
_RECIPE_TEMPLATE = _RECIPE_ENV.get_template("conanfile.py.j2")


def to_conan_reference(ref: Ref) -> str:
    """Encode a reference, rejecting names that cannot be represented without loss."""
    ref = normalize_ref(ref)
    if ref.namespace.startswith("-") or not all(
        _TOKEN.fullmatch(value) for value in (ref.namespace, ref.component, ref.view, ref.type)
    ):
        raise InvalidPackage("Conan coordinates require lowercase tokens without dots or slashes")
    name = ref.package_name
    value = str(ref)
    if (
        len(name) > _MAX_NAME_LENGTH
        or len(value) > _MAX_REFERENCE_LENGTH
        or not _VERSION.fullmatch(ref.version)
    ):
        raise InvalidPackage("Reference exceeds Conan name/version constraints")
    return value


def from_conan_reference(value: str, *, catalog: RefCatalog) -> Ref:
    """Decode a recipe reference; revisions and timestamps are transport details."""
    try:
        name, version = value.split("#", 1)[0].split("/")
        namespace, component, view, package_type = name.split(".")
        ref = Ref(namespace, component, view, package_type, version, catalog=catalog)
        if to_conan_reference(ref) != value.split("#", 1)[0]:
            raise ValueError("Noncanonical coordinates")
        return ref
    except (ValueError, InvalidPackage) as exc:
        raise InvalidPackage(f"Not a catalog Conan reference: {value!r}") from exc


def search_pattern(query: SearchQuery) -> str | None:
    """Use wildcards only for unspecified fields, never for literal query text."""
    fields = (query.namespace, query.component, query.view, query.type)
    if any(value is not None and not _TOKEN.fullmatch(value) for value in fields):
        return None
    if query.version is not None and not _VERSION.fullmatch(query.version):
        return None
    name = ".".join(value if value is not None else "*" for value in fields)
    return f"{name}/{query.version if query.version is not None else '*'}#*"


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


class _ConanData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    scm: _SCM


def write_recipe(directory: Path, record: LocalRecord, requirements: tuple[str, ...] = ()) -> None:
    """Write package metadata; runtime file paths never enter the exported record."""
    requirements = tuple(sorted(set(requirements)))
    source = record.source
    metadata = RecipeMetadata(format=5, requires=requirements, has_source=bool(source))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "conanfile.py").write_text(_recipe_text(record.ref, requirements))
    (directory / "record.json").write_text(record.to_json())
    (directory / "hwcatalog.json").write_text(metadata.model_dump_json())
    if record.source is not None:
        (directory / "source-path.txt").write_text(str(record.source))


def read_recipe(directory: Path, expected: Ref) -> tuple[Record, RecipeMetadata]:
    """Validate the exported format before allowing Conan to evaluate the recipe."""
    try:
        record = normalize_record(
            Record.from_json(
                (directory / "record.json").read_text(),
                package_dir=directory,
                catalog=expected._catalog,
            )
        )
        metadata = RecipeMetadata.model_validate_json((directory / "hwcatalog.json").read_text())
        if metadata.has_source:
            data = _ConanData.model_validate(
                yaml.safe_load((directory / "conandata.yml").read_text())
            )
            record = normalize_record(
                replace(
                    record,
                    scm=data.scm.model_dump(),
                )
            )
        if record.ref != expected:
            raise ValueError("Record coordinates disagree with the recipe")
        _validate_recipe(directory, record, metadata)
        return record, metadata
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


def _validate_recipe(directory: Path, record: Record, metadata: RecipeMetadata) -> None:
    for dependency in metadata.requires:
        if (
            to_conan_reference(from_conan_reference(dependency, catalog=record.ref._catalog))
            != dependency.split("#", 1)[0]
        ):
            raise ValueError("Requirements must use canonical exact references")
    if (directory / "conanfile.py").read_text() != _recipe_text(record.ref, metadata.requires):
        raise ValueError("Only canonical catalog recipes are supported")
