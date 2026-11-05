"""Fetch package artifacts and optional pinned SCM sources."""

from pathlib import Path
from tempfile import TemporaryDirectory

from ...api.errors import CatalogError
from ...api.models.records import Ref
from ...api.models.resources import MaterializedPackage
from ..validation import normalize_ref
from . import graph, records
from .client import ConanClient
from .format import (
    read_recipe,
    to_conan_reference,
)


def materialize(
    client: ConanClient, ref: Ref, *, with_sources: bool = False
) -> MaterializedPackage:
    """Install artifacts and optionally run Conan source retrieval for this package."""
    ref = normalize_ref(ref)
    pinned, directory = records.locate(client, ref)
    record, _ = read_recipe(directory, ref)
    if not record.source and not record.files:
        raise CatalogError("Package has no stored source or artifacts")
    with TemporaryDirectory(prefix="hwcatalog-conan-install-") as temporary:
        result = client.install(pinned, Path(temporary), with_sources=with_sources)
        graph.dependency_records(client, result)
        package = result.root.conanfile.dependencies.host[to_conan_reference(ref)]
        if package.package_folder is None:
            raise CatalogError("Conan did not install the data package")
        source_path = None
        if with_sources and record.source is not None:
            if package.folders.source_folder is None:
                raise CatalogError("Conan did not provide the SCM source folder")
            source_path = Path(package.folders.source_folder) / "source"
        return MaterializedPackage(Path(package.package_folder), source_path)
