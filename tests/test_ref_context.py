"""Coordinates are portable values; operation context belongs to catalogs and graphs."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from coreref.api.catalog import Catalog
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Ref
from coreref.testing import local_server


@pytest.fixture
def other_catalog(tmp_path: Path) -> Iterator[Catalog]:
    with local_server(tmp_path / "other-server") as server:
        yield Catalog(server.configure(tmp_path / "other-home"))


def test_one_reference_can_be_used_with_distinct_catalogs(
    catalog: Catalog, other_catalog: Catalog
) -> None:
    ref = Ref("soc.cpu.rtl.default/1.0")
    first = catalog.publish(LocalRecord(ref, {"origin": "first"}))
    second = other_catalog.publish(LocalRecord(ref, {"origin": "second"}))
    assert catalog.info(ref).payload == {"origin": "first"}
    assert other_catalog.get(ref).payload == {"origin": "second"}
    assert Graph(catalog, ref).record.ref == first.ref
    assert Graph(other_catalog, ref).record.ref == second.ref
    assert catalog.search(SearchQuery())[0].ref == first.ref
    assert other_catalog.search_refs(SearchQuery()) == (second.ref,)
    assert ref == Ref(str(ref)) and hash(ref) == hash(Ref(str(ref)))


def test_graph_uses_the_explicit_catalog_for_all_nodes(catalog: Catalog) -> None:
    leaf = catalog.publish(LocalRecord(Ref("soc.leaf.rtl.default/1.0")))
    root = LocalRecord(Ref("soc.root.rtl.default/1.0"))
    graph = Graph(catalog, root, dependencies=(leaf.ref,))
    assert graph.catalog is catalog
    assert graph.get()[0].ref == leaf.ref
    assert all(not hasattr(record.ref, "_catalog") for record in graph.records)
    assert graph.requirements == (leaf.ref,)


def test_reference_has_no_client_context_and_read_only_coordinates() -> None:
    ref = Ref("soc.cpu.rtl.default/1.0#abc123")
    assert not hasattr(ref, "_catalog") and not hasattr(ref, "catalog")
    assert repr(ref) == "Ref('soc.cpu.rtl.default/1.0#abc123')"
    assert ref.without_revision() == Ref("soc.cpu.rtl.default/1.0")
    assert Ref.from_fields(**ref.to_dict()) == ref
    with pytest.raises(AttributeError):
        ref.component = "changed"  # type: ignore[misc]
