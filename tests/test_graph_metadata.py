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
    leaf = catalog.publish(LocalRecord(Ref.from_fields("soc", "leaf", "rtl", "default", "1.0")))
    parent = catalog.publish(
        LocalRecord(Ref.from_fields("soc", "parent", "rtl", "default", "1.0")),
        dependencies=(leaf.ref,),
    )
    local = LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0"), {"root": True})
    graph = Graph(catalog, local, dependencies=(parent.ref, leaf.ref))
    newer = catalog.publish(LocalRecord(leaf.ref.without_revision(), {"generation": 2}))
    with patch.object(
        catalog._client, "run", side_effect=AssertionError("Metadata must not call Conan")
    ):
        assert graph.info(leaf.ref.without_revision()) is graph.info(leaf.ref)
        assert graph.info(leaf.ref).payload is None
        assert graph.dependencies(parent.ref.without_revision()) == (leaf,)
        assert graph.dependencies() == (leaf, parent)
        assert graph.dependencies(local.ref, filters=SearchQuery(component="parent")) == (parent,)
        assert graph.dependencies(leaf.ref) == ()
        assert graph.dependencies(filters=SearchQuery(view="be")) == ()
        for operation in (graph.info, graph.dependencies):
            with pytest.raises(InvalidPackage, match="Revision"):
                operation(newer.ref)
            with pytest.raises(InvalidPackage, match="Unknown reference"):
                operation(Ref.from_fields("soc", "absent", "rtl", "default", "1.0"))
        snapshot = graph.info(local.ref)
        assert isinstance(snapshot, LocalRecord)
        snapshot.payload = {"edited": True}
        assert graph.info(local.ref).payload == {"root": True}


def test_unknown_refs_use_one_error_contract_without_conan_io(catalog: Catalog) -> None:
    graph = Graph(catalog, LocalRecord(Ref("test.root.rtl.default/1.0")))
    missing = Ref("test.missing.rtl.default/1.0")
    operations = (
        lambda: graph.info(missing),
        lambda: graph.dependencies(missing),
        lambda: graph.ordered_refs(missing),
        lambda: graph.ordered_records(missing),
        lambda: graph.get((missing,)),
        lambda: graph.get_with_sources((missing,)),
    )
    with patch.object(catalog._client, "run", side_effect=AssertionError("Unexpected I/O")):
        for operation in operations:
            with pytest.raises(InvalidPackage, match="Unknown reference"):
                operation()
