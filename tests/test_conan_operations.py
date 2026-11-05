"""Conan performs resolution once; direct queries read canonical requirements."""

from dataclasses import asdict
from pathlib import Path

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from hwcatalog.backends.conan import ConanBackend
from tests.test_packages import description


@pytest.mark.parametrize("locked", [False, True])
def test_install_resolves_once_and_preserves_project_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, locked: bool
) -> None:
    backend = ConanBackend(tmp_path / "cache")
    with Catalog(backend) as catalog:
        rtl = catalog.publish(Record(Ref("cpu", "1.0", "rtl", "soc")))
        artifact = tmp_path / "cpu.lib"
        artifact.write_text("library(cpu) {}")
        document = description(
            tmp_path, files={"cpu.lib": str(artifact)}, dependencies=[asdict(rtl.ref)]
        )
        record = catalog.publish_file(document)
        lock = catalog.create_lockfile((rtl.ref,), tmp_path / "conan.lock") if locked else None
        original = lock.read_bytes() if lock else None
        operations: list[tuple[str, ...]] = []
        run = backend._client.run

        def track(*arguments: str) -> object:
            operations.append(arguments)
            return run(*arguments)

        monkeypatch.setattr(backend._client, "run", track)
        package = catalog.materialize(record.ref, lockfile=lock)
        assert (package.path / "artifacts/cpu.lib").read_text() == artifact.read_text()
        assert sum(args[0] == "install" for args in operations) == 1
        assert not any(args[:2] in (("graph", "info"), ("lock", "create")) for args in operations)
        if lock:
            assert lock.read_bytes() == original


def test_direct_queries_do_not_resolve_dependency_graphs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = ConanBackend(tmp_path / "cache")
    with Catalog(backend) as catalog:
        rtl = catalog.publish(Record(Ref("cpu", "1.0", "rtl", "soc")))
        macro = catalog.publish(
            Record(Ref("cpu", "4.0", "lib-lef", "soc")), dependencies=(rtl.ref,)
        )
        run = backend._client.run

        def track(*arguments: str) -> object:
            assert arguments[:2] != ("graph", "info")
            return run(*arguments)

        monkeypatch.setattr(backend._client, "run", track)
        assert catalog.graph.dependencies(macro.ref) == (rtl,)
        assert catalog.graph.consumers(rtl.ref) == (macro,)
        assert catalog.find_dependents(
            rtl.ref, candidates=SearchQuery(kind="lib-lef"), transitive=False
        ) == (macro,)
