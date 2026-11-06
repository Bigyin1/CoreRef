"""Conan performs resolution once; direct queries read canonical requirements."""

from pathlib import Path

import pytest

from examples.conan_config import configured_home
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.graph import Graph
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import Record, Ref


@pytest.mark.parametrize("locked", [False, True])
def test_materialize_uses_published_requirements_without_project_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked: bool
) -> None:
    connection = Catalog(configured_home(tmp_path / "cache"))
    catalog = connection
    rtl = catalog.publish(Record(Ref("soc", "cpu", "rtl", "default", "1.0")))
    artifact = tmp_path / "cpu.lib"
    artifact.write_text("library(cpu) {}")
    record = catalog.publish(
        Record(Ref("soc", "cpu", "be", "lib-lef", "4.0")),
        dependencies=(rtl.ref,),
        files={"cpu.lib": artifact},
    )
    project = Graph(
        catalog, Record(Ref("soc", "top", "rtl", "default", "1.0")), dependencies=(record.ref,)
    )
    lock = project.create_lockfile(tmp_path / "conan.lock") if locked else None
    graph = Graph(
        catalog,
        Record(Ref("soc", "top", "rtl", "default", "1.0")),
        dependencies=(record.ref,),
        lockfile=lock,
    ).resolve()
    assert record in graph.records
    if lock:
        lock.unlink()
    caller = tmp_path / "caller"
    caller.mkdir()
    (caller / "conan.lock").write_text("unrelated malformed lockfile")
    monkeypatch.chdir(caller)
    operations: list[tuple[str, ...]] = []
    run = connection._client.run

    def track(*arguments: str) -> object:
        operations.append(arguments)
        return run(*arguments)

    monkeypatch.setattr(connection._client, "run", track)
    package = record.ref.materialize()
    assert (package.path / "artifacts/cpu.lib").read_text() == artifact.read_text()
    assert sum(args[0] == "install" for args in operations) == 1
    assert not any(args[:2] == ("graph", "info") or args[0] == "lock" for args in operations)


def test_direct_queries_do_not_resolve_dependency_graphs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = Catalog(configured_home(tmp_path / "cache"))
    catalog = connection
    rtl = catalog.publish(Record(Ref("soc", "cpu", "rtl", "default", "1.0")))
    macro = catalog.publish(
        Record(Ref("soc", "cpu", "be", "lib-lef", "4.0")), dependencies=(rtl.ref,)
    )
    run = connection._client.run

    def track(*arguments: str) -> object:
        assert arguments[:2] != ("graph", "info")
        return run(*arguments)

    monkeypatch.setattr(connection._client, "run", track)
    assert macro.ref.dependencies() == (rtl,)
    assert rtl.ref.find_dependents(candidates=SearchQuery(), transitive=False) == (macro,)
    assert rtl.ref.find_dependents(
        candidates=SearchQuery(type="lib-lef", view="be"), transitive=False
    ) == (macro,)
