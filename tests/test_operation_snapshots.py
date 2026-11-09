"""Each operation detaches mutable project inputs before calling Conan."""

from __future__ import annotations

from pathlib import Path

import pytest

from coreref._internal.types import NativeGraph
from coreref.api.catalog import Catalog
from coreref.api.graph import Graph
from coreref.api.records import JSON, LocalRecord, Ref
from tests.test_models import UserPayload


def test_construction_snapshots_mutable_project(catalog: Catalog, tmp_path: Path) -> None:
    items: list[JSON] = ["original"]
    payload = UserPayload({"items": items})
    files = {"report": tmp_path / "original.txt"}
    local = LocalRecord(
        Ref.from_fields("soc", "top", "rtl", "default", "1.0"),
        payload,
        files=files,
    )
    project = Graph(catalog, local)
    first = project.records[0]
    assert isinstance(first, LocalRecord)
    assert payload.calls == 1
    items.append("changed")
    files.clear()
    local.source = tmp_path
    second = Graph(catalog, local).records[0]
    assert isinstance(second, LocalRecord)
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
        Ref.from_fields("soc", "top", "be", "default", "1.0"),
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
    assert catalog.get(stored.ref).payload == {"original": True}
    assert local.payload == {"changed": True} and local.files == {}


def test_construction_resolves_once_and_snapshots_before_conan(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    dependency = catalog.publish(
        LocalRecord(Ref.from_fields("soc", "leaf", "rtl", "default", "1.0"))
    )
    local = LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0"))
    resolve = catalog._client.graph
    calls = 0

    def change_inputs(
        directory: Path, *, lockfile: Path | None = None, update: bool = False
    ) -> NativeGraph:
        nonlocal calls
        calls += 1
        local.payload = {"edited": True}
        return resolve(directory, lockfile=lockfile, update=update)

    monkeypatch.setattr(catalog._client, "graph", change_inputs)
    project = Graph(catalog, local, dependencies=(dependency.ref,))
    assert calls == 1
    assert project.networkx.number_of_edges() == 1
    assert next(record for record in project.records if record.ref == local.ref).payload is None
    assert project.ordered_refs(local.ref) == (dependency.ref, local.ref)
    assert project.ordered_refs(local.ref) == (dependency.ref, local.ref)
    assert calls == 1
