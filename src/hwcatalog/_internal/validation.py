"""Catalog input normalization, independent of Conan graph resolution."""

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path, PurePosixPath

from ..api.errors import InvalidPackage
from ..api.protocols import RefCatalog
from ..api.query import SearchQuery
from ..api.records import LocalRecord, Record, Ref


def _text(value: str, *, blank: bool = False) -> str:
    if not isinstance(value, str) or (not blank and not value.strip()):
        raise InvalidPackage("Expected a nonempty string" if not blank else "Expected a string")
    return str(value)


def _optional_text(value: str | None) -> str | None:
    return None if value is None else _text(value, blank=True)


def normalize_ref(value: Ref) -> Ref:
    return Ref(
        _text(value.namespace),
        _text(value.component),
        _text(value.view),
        _text(value.type),
        _text(value.version),
        catalog=value._catalog,
    )


def normalize_record(value: Record, *, catalog: RefCatalog | None = None) -> Record:
    _validate_paths(value.files, artifacts=True)
    _validate_paths(value.nfs)
    record = replace(value, ref=normalize_ref(value.ref))
    return Record.from_json(
        json.dumps(record.to_dict(), allow_nan=False),
        catalog=catalog if catalog is not None else value.ref._catalog,
    )


def normalize_local(value: LocalRecord, *, catalog: RefCatalog | None = None) -> LocalRecord:
    """Freeze project data while retaining its local filesystem paths."""
    if not isinstance(value, LocalRecord):
        raise InvalidPackage("Expected one LocalRecord")
    snapshot = normalize_record(
        Record(value.ref, value.payload, files=value.files, nfs=value.nfs), catalog=catalog
    )
    source_path = value.source
    if source_path is not None:
        source_path = source_path.expanduser().absolute()
    return LocalRecord(snapshot.ref, snapshot.payload, source_path, snapshot.files, snapshot.nfs)


def normalize_publication(
    record: LocalRecord, dependencies: tuple[Ref, ...], *, catalog: RefCatalog | None = None
) -> tuple[LocalRecord, tuple[Ref, ...]]:
    """Validate a local package description and its direct dependencies."""
    record = normalize_local(record, catalog=catalog)
    if any(not isinstance(ref, Ref) for ref in dependencies):
        raise InvalidPackage("Dependencies must be Ref values")
    dependencies = tuple(normalize_ref(ref) for ref in dependencies)
    if len(dependencies) != len(set(dependencies)):
        raise InvalidPackage("Duplicate dependency")
    if record.ref in dependencies:
        raise InvalidPackage("A package cannot depend on itself")
    return record, dependencies


def _validate_paths(paths: Mapping[str, Path], *, artifacts: bool = False) -> None:
    for name, path in paths.items():
        _text(name)
        if artifacts:
            validate_file_name(name)
        if not isinstance(path, Path) or not path.is_absolute():
            raise InvalidPackage("Resource paths must be absolute Path values")


def normalize_query(value: SearchQuery) -> SearchQuery:
    return SearchQuery(
        namespace=_optional_text(value.namespace),
        component=_optional_text(value.component),
        view=_optional_text(value.view),
        type=_optional_text(value.type),
        version=_optional_text(value.version),
    )


def validate_file_name(name: str) -> None:
    path = PurePosixPath(_text(name))
    if path.is_absolute() or any(char in name for char in ("\\", ":", "\0")):
        raise InvalidPackage("Artifact names must be relative POSIX paths")
    if ".." in path.parts or str(path) != name or not path.parts:
        raise InvalidPackage("Artifact names must be canonical without parent traversal")
