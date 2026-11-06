"""Project graphs own opaque Conan lockfiles outside published packages."""

from pathlib import Path

import pytest

from examples.conan_config import configured_home
from hwcatalog._internal.conan import records as conan_records
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError, InvalidPackage
from hwcatalog.api.graph import Dependency, Graph
from hwcatalog.api.records import Record, Ref
from tests.helpers import resolve_published


def test_project_lock_pins_publication_and_installation(tmp_path: Path) -> None:
    catalog = Catalog(configured_home(tmp_path / "cache"))
    dependency = catalog.publish(Record(Ref("soc", "cpu", "rtl", "default", "1.0")))
    result = Record(Ref("soc", "cpu", "be", "lib-lef", "4.0"))
    artifact = tmp_path / "cpu.lib"
    artifact.write_text("library(cpu) {}")
    project = Graph(catalog, result, dependencies=(dependency.ref,))
    lockfile = project.create_lockfile(tmp_path / "project/conan.lock")
    original_lock = lockfile.read_bytes()
    record = catalog.publish(
        result, dependencies=project.dependencies, files={"cpu.lib": artifact}, lockfile=lockfile
    )
    assert record.files == ("cpu.lib",)
    _, recipe = conan_records.locate(catalog._client, result.ref)
    assert not (recipe / "conan.lock").exists()
    assert project.resolve().dependencies == (Dependency(result.ref, dependency.ref),)
    installed = record.ref.materialize()
    assert (installed.path / "artifacts/cpu.lib").read_text() == artifact.read_text()
    assert not (installed.path / "conan.lock").exists()
    assert lockfile.read_bytes() == original_lock
    assert project.create_lockfile() == lockfile
    assert resolve_published(catalog, result.ref, lockfile=lockfile).records


@pytest.mark.parametrize(
    "damage,exception",
    [("missing", CatalogError), ("incomplete", InvalidPackage), ("malformed", CatalogError)],
)
def test_conan_validates_the_supplied_project_lockfile(
    tmp_path: Path, damage: str, exception: type[Exception]
) -> None:
    catalog = Catalog(configured_home(tmp_path / "cache"))
    rtl = catalog.publish(Record(Ref("soc", "cpu", "rtl", "default", "1.0")))
    macro = catalog.publish(
        Record(Ref("soc", "cpu", "be", "lib-lef", "4.0")), dependencies=(rtl.ref,)
    )
    project = Graph(catalog, macro, dependencies=(rtl.ref,))
    lockfile = project.create_lockfile(tmp_path / "project/conan.lock")
    if damage == "missing":
        lockfile.unlink()
    elif damage == "incomplete":
        catalog._client.run(
            "lock",
            "remove",
            "--requires=soc.cpu.rtl.default/*",
            "--lockfile",
            str(lockfile),
            "--lockfile-out",
            str(lockfile),
        )
    else:
        lockfile.write_text("not a Conan lockfile\n")
    with pytest.raises(exception):
        project.resolve()
    assert macro.ref.get() == macro


def test_empty_project_lockfile(tmp_path: Path) -> None:
    catalog = Catalog(configured_home(tmp_path / "cache"))
    root = Record(Ref("soc", "root", "rtl", "default", "1.0"))
    project = Graph(catalog, root, lockfile=tmp_path / "empty/conan.lock")
    lockfile = project.create_lockfile()
    empty_lock = lockfile.read_bytes()
    catalog.publish(root, lockfile=lockfile)
    assert project.resolve().records == (root,)
    consumer = Record(Ref("soc", "consumer", "rtl", "default", "1.0"))
    Graph(catalog, consumer, dependencies=(root.ref,)).create_lockfile(lockfile)
    project.create_lockfile()
    assert lockfile.read_bytes() == empty_lock
