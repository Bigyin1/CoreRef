"""Each operation detaches mutable project inputs before calling Conan."""

from pathlib import Path

import pytest

from hwcatalog._internal.conan.types import NativeGraph
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.graph import Graph
from hwcatalog.api.records import LocalRecord, Ref
from hwcatalog.api.types import JSON
from tests.test_models import UserPayload


def test_resolve_snapshots_mutable_project_on_each_call(catalog: Catalog, tmp_path: Path) -> None:
    items: list[JSON] = ["original"]
    payload = UserPayload({"items": items})
    files = {"report": tmp_path / "original.txt"}
    local = LocalRecord(
        Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog), payload, files=files
    )
    project = Graph(catalog, local)
    first = project.resolve().records[0]
    assert payload.calls == 1
    items.append("changed")
    files.clear()
    local.source = tmp_path
    second = project.resolve().records[0]
    assert payload.calls == 2
    assert first is not local and second is not local
    assert first.payload == {"items": ("original",)}
    assert first.files == {"report": tmp_path / "original.txt"}
    assert first.source is None
    assert second.payload == {"items": ("original", "changed")}
    assert second.files == {} and second.source == tmp_path
    assert isinstance(first, LocalRecord)
    first.payload = {"result-only": True}
    assert local.payload is payload
    assert second.payload == {"items": ("original", "changed")}


def test_publish_snapshots_before_conan_commands(
    catalog: Catalog, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = tmp_path / "report.txt"
    artifact.write_text("report")
    payload = UserPayload({"original": True})
    local = LocalRecord(
        Ref("soc", "top", "be", "default", "1.0", catalog=catalog),
        payload,
        files={"report": artifact},
    )
    export = catalog._client.export_package

    def change_inputs(directory: Path, *, lockfile: Path | None = None) -> NativeGraph:
        local.payload = {"changed": True}
        local.files = {}
        payload.data = None
        return export(directory, lockfile=lockfile)

    monkeypatch.setattr(catalog._client, "export_package", change_inputs)
    stored = catalog.publish(local)
    assert payload.calls == 1
    assert stored.payload == {"original": True}
    assert stored.files["report"].read_text() == "report"
    assert stored.ref.get().payload == {"original": True}
    assert local.payload == {"changed": True} and local.files == {}


def test_graph_changes_during_resolution_do_not_change_result(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependency = catalog.publish(
        LocalRecord(Ref("soc", "leaf", "rtl", "default", "1.0", catalog=catalog))
    )
    local = LocalRecord(Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog))
    project = Graph(catalog, local, dependencies=(dependency.ref,))
    resolve = catalog._client.graph

    def change_inputs(directory: Path, *, lockfile: Path | None = None) -> NativeGraph:
        local.payload = {"edited": True}
        project.dependencies = ()
        return resolve(directory, lockfile=lockfile)

    monkeypatch.setattr(catalog._client, "graph", change_inputs)
    result = project.resolve()
    assert len(result.dependencies) == 1
    assert next(record for record in result.records if record.ref == local.ref).payload is None
    assert project.resolve().dependencies == ()
