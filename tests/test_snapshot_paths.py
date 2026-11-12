"""Resolved paths must remain tied to the project that created the snapshot."""

from pathlib import Path

import pytest

from coreref.api.catalog import Catalog
from coreref.api.graph import Graph
from coreref.api.records import LocalRecord, Ref

pytestmark = pytest.mark.conan_remote


def test_publish_and_lockfile_stay_in_original_project_after_chdir(
    catalog: Catalog,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "result").write_text("first artifact")
    (second / "result").write_text("second artifact")
    monkeypatch.chdir(first)
    record = LocalRecord(Ref("test.cpu.be.report/1.0"), files={"result": Path("result")})
    Graph(catalog, record).create_lockfile("deps.lock")
    graph = Graph(catalog, record, lockfile=Path("deps.lock"))
    monkeypatch.chdir(second)
    snapshot = graph.record
    assert isinstance(snapshot, LocalRecord)
    assert snapshot.files["result"] == first / "result"
    assert graph.lockfile == first / "deps.lock"
    assert graph.create_lockfile() == first / "deps.lock"
    assert not (second / "deps.lock").exists()
    assert catalog.publish(graph).files["result"].read_text() == "first artifact"
