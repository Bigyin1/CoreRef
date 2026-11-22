"""Snapshot queries neither fetch packages nor observe newer remote revisions."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import InvalidPackage
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Ref


def test_metadata_access_reads_the_captured_diamond_without_io(catalog: Catalog) -> None:
    leaf = catalog.publish(LocalRecord(Ref.from_fields("soc", "leaf", "1.0")))
    hidden = catalog.publish(LocalRecord(Ref("soc.hidden/1.0")))
    parent = catalog.publish(
        LocalRecord(Ref.from_fields("soc", "parent", "1.0")),
        dependencies=(leaf.ref, hidden.ref),
    )
    local = LocalRecord(Ref.from_fields("soc", "top", "1.0"), {"root": True})
    graph = Graph(catalog, local, dependencies=(parent.ref, leaf.ref))
    newer = catalog.publish(LocalRecord(leaf.ref.without_revision(), {"generation": 2}))
    with patch.object(
        catalog._client, "run", side_effect=AssertionError("Metadata must not call Conan")
    ):
        assert graph.info(leaf.ref.without_revision()) is graph.info(leaf.ref)
        assert graph.info(leaf.ref).payload is None
        assert set(graph.dependencies(parent.ref.without_revision())) == {leaf, hidden}
        assert graph.dependencies() == (leaf, parent)
        assert graph.dependencies(transitive=True, stop_at={parent.ref.without_revision()}) == (
            leaf,
            parent,
        )
        assert graph.dependencies(parent.ref, transitive=True, stop_at={parent.ref}) == ()
        assert graph.dependencies(local.ref, filters=SearchQuery(component="parent")) == (parent,)
        assert graph.dependencies(leaf.ref) == ()
        assert graph.dependencies(filters=SearchQuery(component="be")) == ()
        for operation in (graph.info, graph.dependencies):
            with pytest.raises(InvalidPackage, match="Revision"):
                operation(newer.ref)
            with pytest.raises(InvalidPackage, match="Unknown reference"):
                operation(Ref.from_fields("soc", "absent", "1.0"))
        snapshot = graph.info(local.ref)
        assert isinstance(snapshot, LocalRecord)
        snapshot.payload = {"edited": True}
        assert graph.info(local.ref) is local and local.payload == {"edited": True}


def test_unknown_refs_use_one_error_contract_without_conan_io(catalog: Catalog) -> None:
    graph = Graph(catalog, LocalRecord(Ref("test.root/1.0")))
    missing = Ref("test.missing/1.0")
    operations = (
        lambda: graph.info(missing),
        lambda: graph.dependencies(missing),
        lambda: graph.dependencies(missing, transitive=True),
        lambda: graph.get((missing,)),
        lambda: graph.get((missing,), with_sources=True),
    )
    with patch.object(catalog._client, "run", side_effect=AssertionError("Unexpected I/O")):
        for operation in operations:
            with pytest.raises(InvalidPackage, match="Unknown reference"):
                operation()
