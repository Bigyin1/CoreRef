"""Prepared publication preserves one metadata and dependency snapshot across later changes."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, InvalidPackage
from coreref.api.graph import Graph
from coreref.api.records import LocalRecord, Ref


class Payload:
    def __init__(self) -> None:
        self.calls = 0
        self.text = "captured"

    def to_json(self) -> str:
        self.calls += 1
        return '{"value": "' + self.text + '"}'


def test_publish_graph_keeps_its_metadata_files_and_revision_choice(
    catalog: Catalog, tmp_path: Path
) -> None:
    dependency = Ref.from_fields("soc", "leaf", "rtl", "default", "1.0")
    first = catalog.publish(LocalRecord(dependency, {"generation": 1}))
    artifact = tmp_path / "result.lib"
    artifact.write_text("first file")
    payload = Payload()
    local = LocalRecord(
        Ref.from_fields("soc", "macro", "be", "lib-lef", "2.0"),
        payload,
        files={"lib": artifact},
    )
    graph = Graph(catalog, local, dependencies=(dependency,))
    local.files = {}
    payload.text = "edited"
    catalog.publish(LocalRecord(dependency, {"generation": 2}))
    with patch.object(catalog._client, "run", wraps=catalog._client.run) as commands:
        published = catalog.publish(graph)
    assert published.payload == {"value": "captured"} and payload.calls == 1
    assert published.files["lib"].read_text() == "first file"
    assert Graph(catalog, published.ref).dependencies() == (first,)
    assert not any(call.args[:2] == ("graph", "info") for call in commands.call_args_list)
    assert sum(call.args[0] == "export-pkg" for call in commands.call_args_list) == 1
    assert catalog.publish(graph).ref == published.ref


def test_publish_resolved_graph_uses_captured_lock_after_original_is_deleted(
    catalog: Catalog, tmp_path: Path
) -> None:
    ref = Ref.from_fields("soc", "leaf", "rtl", "default", "1.0")
    first = catalog.publish(LocalRecord(ref, {"generation": 1}))
    root = LocalRecord(Ref.from_fields("soc", "parent", "rtl", "default", "1.0"))
    lock = Graph(catalog, root, dependencies=(ref,)).create_lockfile(tmp_path / "project.lock")
    catalog.publish(LocalRecord(ref, {"generation": 2}))
    prepared = Graph(catalog, root, dependencies=(ref,), lockfile=lock)
    lock.unlink()
    published = catalog.publish(prepared)
    assert Graph(catalog, published.ref).dependencies() == (first,)
    assert not lock.exists()


def test_prepared_upload_failure_can_be_retried_without_resolving_again(catalog: Catalog) -> None:
    ref = Ref.from_fields("soc", "local", "be", "report", "1.0")
    graph = Graph(catalog, LocalRecord(ref, {"report": True}))
    with (
        patch.object(catalog._client, "upload", side_effect=CatalogError("offline")),
        pytest.raises(CatalogError, match="offline"),
    ):
        catalog.publish(graph)
    with patch.object(
        catalog._client, "graph", side_effect=AssertionError("Must reuse resolution")
    ):
        first = catalog.publish(graph)
        assert catalog.publish(graph) == first


@pytest.mark.parametrize("override", ["dependencies", "lockfile", "catalog", "published"])
def test_prepared_publication_rejects_ambiguous_inputs(
    catalog: Catalog, tmp_path: Path, override: str
) -> None:
    ref = Ref.from_fields("soc", "local", "be", "report", "1.0")
    graph = Graph(catalog, LocalRecord(ref))
    with pytest.raises(InvalidPackage):
        match override:
            case "dependencies":
                catalog.publish(graph, dependencies=(ref,))
            case "lockfile":
                catalog.publish(graph, lockfile=tmp_path / "project.lock")
            case "catalog":
                Catalog(catalog._client.home).publish(graph)
            case "published":
                catalog.publish(Graph(catalog, catalog.publish(graph).ref))
