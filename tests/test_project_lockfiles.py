"""Project graphs own opaque Conan lockfiles outside published packages."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, InvalidPackage
from coreref.api.graph import Graph
from coreref.api.records import LocalRecord, Record, Ref
from tests.helpers import local_record


def test_project_lock_pins_publication_and_installation(
    tmp_path: Path, *, catalog: Catalog
) -> None:
    catalog = Catalog(catalog._client.home)
    dependency = catalog.publish(
        local_record(Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0")))
    )
    artifact = tmp_path / "cpu.lib"
    artifact.write_text("library(cpu) {}")
    project = Graph(
        catalog,
        LocalRecord(Ref.from_fields("soc", "cpu", "be", "lib-lef", "4.0")),
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
    assert tuple(sorted(project.networkx.edges)) == ((project.record.ref, dependency.ref),)
    installed = catalog.get(record.ref)
    assert installed.files["cpu.lib"].read_text() == artifact.read_text()
    assert "conan.lock" not in installed.files
    assert lockfile.read_bytes() == original_lock
    assert project.create_lockfile() == lockfile
    Graph(catalog, record.ref).create_lockfile(tmp_path / "stored.lock")
    assert Graph(catalog, record.ref, lockfile=tmp_path / "stored.lock").records


@pytest.mark.parametrize(
    "damage,exception",
    [("incomplete", InvalidPackage), ("malformed", CatalogError)],
)
def test_conan_validates_the_supplied_project_lockfile(
    tmp_path: Path, damage: str, exception: type[Exception], *, catalog: Catalog
) -> None:
    catalog = Catalog(catalog._client.home)
    rtl = catalog.publish(
        local_record(Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0")))
    )
    macro = catalog.publish(
        local_record(Record(Ref.from_fields("soc", "cpu", "be", "lib-lef", "4.0"))),
        dependencies=(rtl.ref,),
    )
    project = Graph(catalog, local_record(macro), dependencies=(rtl.ref,))
    lockfile = project.create_lockfile(tmp_path / "project/conan.lock")
    if damage == "incomplete":
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
    assert catalog.get(macro.ref) == macro


def test_empty_project_lockfile(tmp_path: Path, *, catalog: Catalog) -> None:
    catalog = Catalog(catalog._client.home)
    root = Record(Ref.from_fields("soc", "root", "rtl", "default", "1.0"))
    project = Graph(catalog, local_record(root))
    lockfile = project.create_lockfile(tmp_path / "empty/conan.lock")
    empty_lock = lockfile.read_bytes()
    root = catalog.publish(local_record(root), lockfile=lockfile)
    assert project.records == (local_record(root),)
    consumer = Record(Ref.from_fields("soc", "consumer", "rtl", "default", "1.0"))
    Graph(catalog, local_record(consumer), dependencies=(root.ref,)).create_lockfile(lockfile)
    project.create_lockfile()
    assert lockfile.read_bytes() == empty_lock


@pytest.mark.parametrize("published", [False, True])
def test_missing_lockfile_is_optional_but_its_output_path_is_retained(
    catalog: Catalog, tmp_path: Path, published: bool
) -> None:
    dependency = LocalRecord(Ref("soc.cpu.rtl.default/1.0"))
    first = catalog.publish(dependency)
    root = dependency.ref if published else LocalRecord(Ref("soc.top.rtl.default/1.0"))
    requirements = () if published else (dependency.ref,)
    path = tmp_path / "new-project/project.lock"
    graph = Graph(catalog, root, dependencies=requirements, lockfile=path)
    assert graph.lockfile == path and not path.exists()
    assert graph.info(dependency.ref).ref == first.ref
    assert graph.create_lockfile() == path
    dependency.payload = {"generation": 2}
    second = catalog.publish(dependency)
    assert second.ref != first.ref
    assert (
        Graph(catalog, root, dependencies=requirements, lockfile=path).info(dependency.ref).ref
        == first.ref
    )


def test_an_existing_directory_cannot_be_ignored_as_a_missing_lockfile(
    catalog: Catalog, tmp_path: Path
) -> None:
    with pytest.raises(CatalogError):
        Graph(catalog, LocalRecord(Ref("soc.top.rtl.default/1.0")), lockfile=tmp_path)
