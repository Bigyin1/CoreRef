"""Conan performs resolution once; direct queries read canonical requirements."""

from pathlib import Path

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.graph import Graph
from hwcatalog.api.records import Record, Ref
from tests.helpers import local_record


@pytest.mark.parametrize("locked", [False, True])
def test_materialize_uses_published_requirements_without_project_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked: bool, *, catalog: Catalog
) -> None:
    catalog = Catalog(catalog._client.home)
    rtl = catalog.publish(
        local_record(
            Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
        )
    )
    artifact = tmp_path / "cpu.lib"
    artifact.write_text("library(cpu) {}")
    record = catalog.publish(
        local_record(
            Record(Ref.from_fields("soc", "cpu", "be", "lib-lef", "4.0", catalog=catalog)),
            files={"cpu.lib": artifact},
        ),
        dependencies=(rtl.ref,),
    )
    project = Graph(
        catalog,
        local_record(
            Record(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=catalog))
        ),
        dependencies=(record.ref,),
    )
    lock = project.create_lockfile(tmp_path / "conan.lock") if locked else None
    assert record in project.records
    if lock:
        lock.unlink()
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "conan.lock").write_text("unrelated malformed lockfile")
    monkeypatch.chdir(caller)
    operations: list[tuple[str, ...]] = []
    run = catalog._client.run

    def track(*arguments: str) -> object:
        operations.append(arguments)
        return run(*arguments)

    monkeypatch.setattr(catalog._client, "run", track)
    package = record.ref.get()
    assert package.files["cpu.lib"].read_text() == artifact.read_text()
    assert sum(args[0] == "install" for args in operations) == 1
    assert not any(args[:2] == ("graph", "info") or args[0] == "lock" for args in operations)


def test_direct_queries_do_not_resolve_dependency_graphs(
    monkeypatch: pytest.MonkeyPatch, *, catalog: Catalog
) -> None:
    connection = Catalog(catalog._client.home)
    catalog = connection
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
    run = connection._client.run

    def track(*arguments: str) -> object:
        assert arguments[:2] != ("graph", "info")
        return run(*arguments)

    monkeypatch.setattr(connection._client, "run", track)
    assert macro.ref.dependencies() == (rtl,)
