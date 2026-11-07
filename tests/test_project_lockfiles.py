"""Project graphs own opaque Conan lockfiles outside published packages."""

from dataclasses import replace
from pathlib import Path

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, InvalidPackage
from coreref.api.graph import Dependency, Graph
from coreref.api.records import LocalRecord, Record, Ref
from tests.helpers import local_record


def test_project_lock_pins_publication_and_installation(
    tmp_path: Path, *, catalog: Catalog
) -> None:
    catalog = Catalog(catalog._client.home)
    dependency = catalog.publish(
        local_record(
            Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
        )
    )
    artifact = tmp_path / "cpu.lib"
    artifact.write_text("library(cpu) {}")
    project = Graph(
        catalog,
        LocalRecord(Ref.from_fields("soc", "cpu", "be", "lib-lef", "4.0", catalog=catalog)),
        dependencies=(dependency.ref,),
    )
    lockfile = project.create_lockfile(tmp_path / "project/conan.lock")
    original_lock = lockfile.read_bytes()
    local = project.record
    assert isinstance(local, LocalRecord)
    record = catalog.publish(
        replace(local, files={"cpu.lib": artifact}),
        dependencies=project.requirements,
        lockfile=lockfile,
    )
    assert tuple(record.files) == ("cpu.lib",)
    recipe = catalog._package(project.record.ref).recipe().directory
    assert not (recipe / "conan.lock").exists()
    assert project.edges == (Dependency(project.record.ref, dependency.ref),)
    installed = record.ref.get()
    assert installed.files["cpu.lib"].read_text() == artifact.read_text()
    assert "conan.lock" not in installed.files
    assert lockfile.read_bytes() == original_lock
    assert project.create_lockfile() == lockfile
    record.ref.graph().create_lockfile(tmp_path / "stored.lock")
    assert record.ref.graph(lockfile=tmp_path / "stored.lock").records


@pytest.mark.parametrize(
    "damage,exception",
    [("missing", CatalogError), ("incomplete", InvalidPackage), ("malformed", CatalogError)],
)
def test_conan_validates_the_supplied_project_lockfile(
    tmp_path: Path, damage: str, exception: type[Exception], *, catalog: Catalog
) -> None:
    catalog = Catalog(catalog._client.home)
    rtl = catalog.publish(
        local_record(
            Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
        )
    )
    macro = catalog.publish(
        local_record(
            Record(Ref.from_fields("soc", "cpu", "be", "lib-lef", "4.0", catalog=catalog))
        ),
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
    local = project.record
    assert isinstance(local, LocalRecord)
    with pytest.raises(exception):
        Graph(catalog, local, dependencies=project.requirements, lockfile=lockfile)
    assert macro.ref.get() == macro


def test_empty_project_lockfile(tmp_path: Path, *, catalog: Catalog) -> None:
    catalog = Catalog(catalog._client.home)
    root = Record(Ref.from_fields("soc", "root", "rtl", "default", "1.0", catalog=catalog))
    project = Graph(catalog, local_record(root))
    lockfile = project.create_lockfile(tmp_path / "empty/conan.lock")
    empty_lock = lockfile.read_bytes()
    root = catalog.publish(local_record(root), lockfile=lockfile)
    assert project.records == (local_record(root),)
    consumer = Record(Ref.from_fields("soc", "consumer", "rtl", "default", "1.0", catalog=catalog))
    Graph(catalog, local_record(consumer), dependencies=(root.ref,)).create_lockfile(lockfile)
    project.create_lockfile()
    assert lockfile.read_bytes() == empty_lock
