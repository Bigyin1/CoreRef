"""Produce local paths from a published package and its optional SCM source."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from frozendict import deepfreeze

from ...api.errors import CatalogError
from ...api.records import Record
from . import graph, records
from .client import ConanClient
from .format import read_recipe, to_conan_reference


def materialize(client: ConanClient, record: Record, *, with_sources: bool = False) -> Record:
    """Get binary file paths and optionally let Conan fetch the pinned Git checkout."""
    pinned, directory = records.locate(client, record.ref)
    record, _ = read_recipe(directory, record.ref)
    if not record.scm and not record.files:
        return record
    with TemporaryDirectory(prefix="hwcatalog-conan-install-") as temporary:
        result = client.install(pinned, Path(temporary), with_sources=with_sources)
        graph.dependency_records(client, result, catalog=record.ref._catalog)
        package = result.root.conanfile.dependencies.host[to_conan_reference(record.ref)]
        if package.package_folder is None:
            raise CatalogError("Conan did not install the data package")
        files = {name: Path(package.package_folder) / "artifacts" / name for name in record.files}
        source = None
        if with_sources and record.scm:
            if package.folders.source_folder is None:
                raise CatalogError("Conan did not provide the SCM source folder")
            source = Path(package.folders.source_folder) / "source"
        return replace(record, source=source, files=deepfreeze(files))
