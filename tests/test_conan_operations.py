"""Conan performs resolution once; direct queries read canonical requirements."""

from dataclasses import asdict
from pathlib import Path

import pytest

from examples.conan_config import configured_home
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from tests.test_packages import description


@pytest.mark.parametrize("locked", [False, True])
def test_materialize_uses_published_requirements_without_project_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked: bool
) -> None:
    connection = Catalog(configured_home(tmp_path / "cache"))
    with connection as catalog:
        rtl = catalog.publish(Record(Ref("cpu", "1.0", "rtl", "soc")))
        artifact = tmp_path / "cpu.lib"
        artifact.write_text("library(cpu) {}")
        document = description(
            tmp_path, files={"cpu.lib": str(artifact)}, dependencies=[asdict(rtl.ref)]
        )
        record = catalog.publish_file(document)
        lock = catalog.create_lockfile((record.ref,), tmp_path / "conan.lock") if locked else None
        graph = catalog.resolve_local(
            Record(Ref("top", "1.0", "rtl", "soc")),
            dependencies=(record.ref,),
            lockfile=lock,
        )
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
        package = catalog.materialize(record.ref)
        assert (package.path / "artifacts/cpu.lib").read_text() == artifact.read_text()
        assert sum(args[0] == "install" for args in operations) == 1
        assert not any(args[:2] == ("graph", "info") or args[0] == "lock" for args in operations)


def test_direct_queries_do_not_resolve_dependency_graphs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    connection = Catalog(configured_home(tmp_path / "cache"))
    with connection as catalog:
        rtl = catalog.publish(Record(Ref("cpu", "1.0", "rtl", "soc")))
        macro = catalog.publish(
            Record(Ref("cpu", "4.0", "lib-lef", "soc")), dependencies=(rtl.ref,)
        )
        run = connection._client.run

        def track(*arguments: str) -> object:
            assert arguments[:2] != ("graph", "info")
            return run(*arguments)

        monkeypatch.setattr(connection._client, "run", track)
        assert catalog.dependencies(macro.ref) == (rtl,)
        assert catalog.find_dependents(rtl.ref, candidates=SearchQuery(), transitive=False) == (
            macro,
        )
        assert catalog.find_dependents(
            rtl.ref, candidates=SearchQuery(kind="lib-lef"), transitive=False
        ) == (macro,)
