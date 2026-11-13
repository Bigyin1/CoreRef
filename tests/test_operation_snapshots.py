"""Short CLI commands share working inputs and stage publication data once."""

from __future__ import annotations

from pathlib import Path

import pytest

from coreref._internal.types import NativeGraph
from coreref.api.catalog import Catalog
from coreref.api.graph import Graph
from coreref.api.records import JSON, LocalRecord, Ref
from tests.test_models import UserPayload


def test_graph_shares_the_mutable_project_without_encoding_payload(
    catalog: Catalog, tmp_path: Path
) -> None:
    items: list[JSON] = ["original"]
    payload = UserPayload({"items": items})
    local = LocalRecord(
        Ref("soc.top.rtl.default/1.0"), payload, files={"report": tmp_path / "original.txt"}
    )
    graph = Graph(catalog, local)
    assert graph.record is local and graph.records[0] is local
    assert payload.calls == 0
    items.append("changed")
    local.files.clear()
    local.source = tmp_path
    assert graph.record.payload is payload
    assert graph.record.files == {} and graph.record.source == tmp_path
    assert Graph(catalog, local).record is local
    assert payload.calls == 0


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


def test_construction_resolves_once_and_keeps_the_working_record(
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
    assert project.info(local.ref) is local and project.record.payload == {"edited": True}
    assert project.ordered_refs(local.ref) == (dependency.ref, local.ref)
    assert project.ordered_refs(local.ref) == (dependency.ref, local.ref)
    assert calls == 1
