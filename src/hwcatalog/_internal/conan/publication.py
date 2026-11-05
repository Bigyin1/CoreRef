"""Publish one package and create project-owned lockfiles."""

from pathlib import Path
from tempfile import TemporaryDirectory

from ...api.errors import Conflict, InvalidPackage
from ...api.models.records import Record, Ref
from ..manifest import Manifest, read_manifest
from ..validation import normalize_publication, normalize_ref
from . import records
from .client import ConanClient
from .format import (
    read_recipe,
    to_conan_reference,
    write_recipe,
)


def publish_file(client: ConanClient, path: str | Path, *, lockfile: Path | None = None) -> Record:
    manifest = read_manifest(Path(path))
    return publish(
        client, manifest.record, manifest.dependencies, manifest=manifest, lockfile=lockfile
    )


def create_lockfile(client: ConanClient, refs: tuple[Ref, ...], path: str | Path) -> Path:
    """Write a Conan lockfile owned by an RTL repository or BE workspace."""
    output = Path(path).expanduser().resolve()
    references = tuple(records.locate(client, normalize_ref(ref))[0] for ref in dict.fromkeys(refs))
    output.parent.mkdir(parents=True, exist_ok=True)
    client.create_project_lockfile(references, output, seed=output if output.is_file() else None)
    return output


def publish(
    client: ConanClient,
    record: Record,
    dependencies: tuple[Ref, ...],
    *,
    manifest: Manifest | None = None,
    lockfile: Path | None = None,
) -> Record:
    record, dependencies = normalize_publication(record, dependencies)
    if record.files and manifest is None:
        raise InvalidPackage("Publish stored files using a package JSON document")
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
            source_path=manifest.source_path if manifest else None,
            files=manifest.files if manifest else None,
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
