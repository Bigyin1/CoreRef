"""References carry operation context without changing stored package identity."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import is_dataclass
from pathlib import Path

import pytest

from coreref.api.catalog import Catalog
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import Record, Ref
from examples.local_server import local_server
from tests.helpers import local_record


@pytest.fixture
def other_catalog(tmp_path: Path) -> Iterator[Catalog]:
    with local_server(tmp_path / "other-server") as server:
        yield Catalog(server.configure(tmp_path / "other-home"))


def test_same_coordinates_use_their_own_catalog(catalog: Catalog, other_catalog: Catalog) -> None:
    other = other_catalog
    first = Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)
    second = Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=other)
    catalog.publish(local_record(Record(first, {"origin": "first"})))
    other.publish(local_record(Record(second, {"origin": "second"})))
    assert first == second
    assert hash(first) == hash(second)
    assert repr(first) == repr(second)
    assert first.to_dict() == {
        "namespace": "soc",
        "component": "cpu",
        "view": "rtl",
        "type": "default",
        "version": "1.0",
    }
    assert json.dumps(Record(first).to_dict(), allow_nan=False) == json.dumps(
        Record(second).to_dict(), allow_nan=False
    )
    assert first.get().payload == {"origin": "first"}
    assert second.get().payload == {"origin": "second"}
    assert first.get().ref._catalog is catalog
    assert second.get().ref._catalog is other
    assert first.graph().record.payload == {"origin": "first"}
    assert second.graph().record.payload == {"origin": "second"}
    assert Graph(local_record(Record(first))).catalog is catalog
    assert Graph(local_record(Record(second))).catalog is other


def test_catalog_creates_operational_refs_in_records_and_graphs(catalog: Catalog) -> None:
    leaf = catalog.publish(
        local_record(
            Record(Ref.from_fields("soc", "leaf", "rtl", "default", "1.0", catalog=catalog))
        )
    )
    root = catalog.publish(
        local_record(
            Record(Ref.from_fields("soc", "root", "rtl", "default", "1.0", catalog=catalog))
        ),
        dependencies=(leaf.ref,),
    )
    for record in (
        *catalog.search(SearchQuery()),
        root.ref.get(),
        *root.ref.graph().dependencies(),
    ):
        assert record.ref._catalog is catalog
        assert record.ref.get() == record
    graph = Graph(
        local_record(
            Record(Ref.from_fields("soc", "local", "rtl", "default", "1.0", catalog=catalog))
        ),
        dependencies=(root.ref,),
    )
    records = {record.ref: record for record in graph.records}
    for graph_record in graph.records:
        assert graph_record.ref._catalog is catalog
    for source, target in graph.networkx.edges:
        assert source is records[source].ref
        assert target is records[target].ref
        assert target.get() == records[target]


def test_reference_requires_a_private_catalog(catalog: Catalog) -> None:
    ref = Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)
    assert ref._catalog is catalog
    assert not hasattr(ref, "catalog")
    assert not is_dataclass(ref)
    assert tuple(name for name in vars(ref) if "catalog" in name) == ("_catalog",)
    with pytest.raises(AttributeError):
        ref.component = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        Ref.from_fields("soc", "cpu", "rtl", "default", "1.0")  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="requires a catalog"):
        Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=None)  # type: ignore[arg-type]
