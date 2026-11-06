"""Project graphs own opaque Conan lockfiles outside published packages."""

from dataclasses import replace
from pathlib import Path

import pytest

from examples.conan_config import configured_home
from hwcatalog._internal.conan import records as conan_records
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError, InvalidPackage
from hwcatalog.api.graph import Dependency, Graph
from hwcatalog.api.records import LocalRecord, Record, Ref
from tests.helpers import local_record, resolve_published


def test_project_lock_pins_publication_and_installation(
    tmp_path: Path, *, catalog: Catalog
) -> None:
    catalog = Catalog(configured_home(tmp_path / "cache"))
    dependency = catalog.publish(
        local_record(Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)))
    )
    artifact = tmp_path / "cpu.lib"
    artifact.write_text("library(cpu) {}")
    project = Graph(
        catalog,
        LocalRecord(Ref("soc", "cpu", "be", "lib-lef", "4.0", catalog=catalog)),
        dependencies=(dependency.ref,),
    )
    lockfile = project.create_lockfile(tmp_path / "project/conan.lock")
    original_lock = lockfile.read_bytes()
    record = catalog.publish(
        replace(project.record, files={"cpu.lib": artifact}),
        dependencies=project.dependencies,
        lockfile=lockfile,
    )
    assert tuple(record.files) == ("cpu.lib",)
    _, recipe = conan_records.locate(catalog._client, project.record.ref)
    assert not (recipe / "conan.lock").exists()
    assert project.resolve().dependencies == (Dependency(project.record.ref, dependency.ref),)
    installed = record.ref.get()
    assert installed.files["cpu.lib"].read_text() == artifact.read_text()
    assert "conan.lock" not in installed.files
    assert lockfile.read_bytes() == original_lock
    assert project.create_lockfile() == lockfile
    assert resolve_published(catalog, project.record.ref, lockfile=lockfile).records


@pytest.mark.parametrize(
    "damage,exception",
    [("missing", CatalogError), ("incomplete", InvalidPackage), ("malformed", CatalogError)],
)
def test_conan_validates_the_supplied_project_lockfile(
    tmp_path: Path, damage: str, exception: type[Exception], *, catalog: Catalog
) -> None:
    catalog = Catalog(configured_home(tmp_path / "cache"))
    rtl = catalog.publish(
        local_record(Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)))
    )
    macro = catalog.publish(
        local_record(Record(Ref("soc", "cpu", "be", "lib-lef", "4.0", catalog=catalog))),
        dependencies=(rtl.ref,),
    )
    project = Graph(catalog, local_record(macro), dependencies=(rtl.ref,))
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


def test_empty_project_lockfile(tmp_path: Path, *, catalog: Catalog) -> None:
    catalog = Catalog(configured_home(tmp_path / "cache"))
    root = Record(Ref("soc", "root", "rtl", "default", "1.0", catalog=catalog))
    project = Graph(catalog, local_record(root), lockfile=tmp_path / "empty/conan.lock")
    lockfile = project.create_lockfile()
    empty_lock = lockfile.read_bytes()
    catalog.publish(local_record(root), lockfile=lockfile)
    assert project.resolve().records == (local_record(root),)
    consumer = Record(Ref("soc", "consumer", "rtl", "default", "1.0", catalog=catalog))
    Graph(catalog, local_record(consumer), dependencies=(root.ref,)).create_lockfile(lockfile)
    project.create_lockfile()
    assert lockfile.read_bytes() == empty_lock
