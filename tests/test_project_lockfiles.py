"""Project-owned Conan lockfiles stay opaque and outside published packages."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from examples.conan_config import configured_home
from hwcatalog._internal.conan import records as conan_records
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError, InvalidPackage
from hwcatalog.api.models.graph import Dependency
from hwcatalog.api.models.records import Record, Ref
from tests.helpers import resolve_published


def test_project_lock_pins_publication_and_installation(tmp_path: Path) -> None:
    connection = Catalog(configured_home(tmp_path / "cache"))
    dependency = Record(Ref("cpu", "1.0", "rtl", "soc"))
    result = Ref("cpu", "4.0", "lib-lef", "soc")
    artifact = tmp_path / "cpu.lib"
    artifact.write_text("library(cpu) {}")
    description = tmp_path / "package.json"
    description.write_text(
        json.dumps(
            {
                "ref": asdict(result),
                "dependencies": [asdict(dependency.ref)],
                "files": {"cpu.lib": str(artifact)},
            }
        )
    )
    catalog = connection
    catalog.publish(dependency)
    lockfile = catalog.create_lockfile((dependency.ref,), tmp_path / "project/conan.lock")
    original_lock = lockfile.read_bytes()
    record = catalog.publish_file(description, lockfile=lockfile)
    assert record.files == ("cpu.lib",)
    _, recipe = conan_records.locate(connection._client, result)
    assert not (recipe / "conan.lock").exists()
    assert resolve_published(catalog, result, lockfile=lockfile).dependencies == (
        Dependency(result, dependency.ref),
    )
    installed = catalog.materialize(result)
    assert (installed.path / "artifacts/cpu.lib").read_text() == artifact.read_text()
    assert not (installed.path / "conan.lock").exists()
    assert lockfile.read_bytes() == original_lock
    assert catalog.create_lockfile((result,), lockfile) == lockfile
    assert resolve_published(catalog, result, lockfile=lockfile).records


@pytest.mark.parametrize(
    "damage,exception",
    [
        ("missing", CatalogError),
        ("incomplete", InvalidPackage),
        ("malformed", CatalogError),
    ],
)
def test_conan_validates_the_supplied_project_lockfile(
    tmp_path: Path, damage: str, exception: type[Exception]
) -> None:
    connection = Catalog(configured_home(tmp_path / "cache"))
    rtl = Record(Ref("cpu", "1.0", "rtl", "soc"))
    macro = Record(Ref("cpu", "4.0", "lib-lef", "soc"))
    catalog = connection
    catalog.publish(rtl)
    catalog.publish(macro, dependencies=(rtl.ref,))
    lockfile = catalog.create_lockfile((rtl.ref,), tmp_path / "project/conan.lock")
    if damage == "missing":
        lockfile.unlink()
    elif damage == "incomplete":
        connection._client.run(
            "lock",
            "remove",
            "--requires=soc.cpu.rtl/*",
            "--lockfile",
            str(lockfile),
            "--lockfile-out",
            str(lockfile),
        )
    else:
        lockfile.write_text("not a Conan lockfile\n")
    with pytest.raises(exception):
        resolve_published(catalog, macro.ref, lockfile=lockfile)
    assert catalog.get(macro.ref) == macro


def test_empty_project_lockfile(tmp_path: Path) -> None:
    catalog = Catalog(configured_home(tmp_path / "cache"))
    lockfile = catalog.create_lockfile((), tmp_path / "empty/conan.lock")
    empty_lock = lockfile.read_bytes()
    root = Record(Ref("root", "1.0", "rtl", "soc"))
    catalog.publish(root, lockfile=lockfile)
    assert resolve_published(catalog, root.ref, lockfile=lockfile).records == (root,)
    catalog.create_lockfile((root.ref,), lockfile)
    catalog.create_lockfile((), lockfile)
    assert lockfile.read_bytes() == empty_lock
