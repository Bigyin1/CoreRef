"""Publish one package from Python data using a generated Conan recipe."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from ...api.errors import Conflict, InvalidPackage
from ...api.protocols import RefCatalog
from ...api.records import Record, Ref
from ..validation import normalize_publication, validate_file_name
from . import records
from .client import ConanClient
from .format import (
    read_recipe,
    to_conan_reference,
    write_recipe,
)


def publish(
    client: ConanClient,
    record: Record,
    dependencies: tuple[Ref, ...],
    *,
    source_path: Path | None = None,
    files: dict[str, Path] | None = None,
    lockfile: Path | None = None,
    catalog: RefCatalog | None = None,
) -> Record:
    record, dependencies = normalize_publication(record, dependencies, catalog=catalog)
    files = _artifact_files(files or {})
    if record.files and record.files != tuple(sorted(files)):
        raise InvalidPackage("Provide the stored files declared in Record.files")
    record = replace(record, files=tuple(sorted(files)))
    if source_path is not None:
        if record.source is not None:
            raise InvalidPackage("Provide either pinned SCM coordinates or a Git checkout")
        source_path = _local_path(source_path)
        if not source_path.is_dir():
            raise InvalidPackage("Git source path must be a repository directory")
    logical = to_conan_reference(record.ref)
    if logical in client.index(f"{logical}*#*"):
        raise Conflict("Record identity already published")
    requirements = tuple(records.requirement(client, target) for target in dependencies)
    with TemporaryDirectory(prefix="hwcatalog-conan-publish-") as temporary:
        directory = Path(temporary)
        write_recipe(
            directory,
            record,
            requirements,
            source_path=source_path,
            files=files,
        )
        graph = client.export_package(directory, lockfile=lockfile)
        reference = graph.root.ref
        if reference is None:
            raise InvalidPackage("Conan did not return the exported recipe reference")
        pinned = reference.repr_notime()
        exported = client.cache_path(pinned, download=False)
        stored, _ = read_recipe(exported, record.ref)
        client.upload(pinned)
        return stored


def _local_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise InvalidPackage(f"Publication input does not exist: {path}") from exc


def _artifact_files(files: dict[str, Path]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for name, path in files.items():
        validate_file_name(name)
        local = _local_path(path)
        if not local.is_file():
            raise InvalidPackage(f"Artifact is not a regular file: {path}")
        if any(name.startswith(other + "/") or other.startswith(name + "/") for other in result):
            raise InvalidPackage("Artifact paths cannot overlap")
        result[name] = local
    return result
