"""Catalog input normalization, independent of Conan graph resolution."""

import re
from dataclasses import replace
from pathlib import PurePosixPath

from ..api.errors import DependencyCycle, InvalidPublication
from ..api.models.query import SearchQuery
from ..api.models.records import Record, Ref


def _text(value: str, *, blank: bool = False) -> str:
    if not isinstance(value, str) or (not blank and not value.strip()):
        raise InvalidPublication("Expected a nonempty string" if not blank else "Expected a string")
    return str(value)


def _optional_text(value: str | None) -> str | None:
    return None if value is None else _text(value, blank=True)


def normalize_ref(value: Ref) -> Ref:
    return Ref(_text(value.name), _text(value.version), _text(value.kind), _text(value.namespace))


def normalize_record(value: Record) -> Record:
    if value.source is not None:
        url = _text(value.source.repository_url)
        if url.startswith("-") or any(char in url for char in ('"', "`", "$", "\n", "\r", "\0")):
            raise InvalidPublication("Git URL contains characters unsafe for Conan SCM helpers")
        if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", value.source.resolved_commit):
            raise InvalidPublication("Git source requires an exact commit")
        if value.source.provider != "git":
            raise InvalidPublication("Only one Git source is supported")
    for name in value.files:
        validate_file_name(name)
    if len(set(value.files)) != len(value.files):
        raise InvalidPublication("Duplicate artifact names")
    record = replace(value, ref=normalize_ref(value.ref))
    return Record.from_json(record.to_json())


def normalize_publication(
    record: Record, dependencies: tuple[Ref, ...]
) -> tuple[Record, tuple[Ref, ...]]:
    """Validate one new package and its direct dependency references."""
    if not isinstance(record, Record):
        raise InvalidPublication("Publish accepts one Record")
    record = normalize_record(record)
    if any(not isinstance(ref, Ref) for ref in dependencies):
        raise InvalidPublication("Dependencies must be Ref values")
    dependencies = tuple(normalize_ref(ref) for ref in dependencies)
    if len(dependencies) != len(set(dependencies)):
        raise InvalidPublication("Duplicate dependency")
    if record.ref in dependencies:
        raise DependencyCycle("A package cannot depend on itself")
    return record, dependencies


def normalize_query(value: SearchQuery) -> SearchQuery:
    return SearchQuery(
        name=_optional_text(value.name),
        version=_optional_text(value.version),
        kind=_optional_text(value.kind),
        namespace=_optional_text(value.namespace),
    )


def validate_file_name(name: str) -> None:
    path = PurePosixPath(_text(name))
    if (
        path.is_absolute()
        or ".." in path.parts
        or any(char in name for char in ("\\", ":", "\0"))
        or str(path) != name
        or name == "."
    ):
        raise InvalidPublication("Artifact names must be canonical relative POSIX paths")
