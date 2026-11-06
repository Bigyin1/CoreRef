"""References carry operation context without changing stored package identity."""

import json
from dataclasses import is_dataclass
from pathlib import Path

import pytest

from examples.conan_config import configured_home
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.graph import Graph
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import Record, Ref
from tests.helpers import local_record


def test_same_coordinates_use_their_own_catalog(catalog: Catalog, tmp_path: Path) -> None:
    other = Catalog(configured_home(tmp_path / "other-home"))
    first = Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)
    second = Ref("soc", "cpu", "rtl", "default", "1.0", catalog=other)
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


def test_catalog_creates_operational_refs_in_records_and_graphs(catalog: Catalog) -> None:
    leaf = catalog.publish(
        local_record(Record(Ref("soc", "leaf", "rtl", "default", "1.0", catalog=catalog)))
    )
    root = catalog.publish(
        local_record(Record(Ref("soc", "root", "rtl", "default", "1.0", catalog=catalog))),
        dependencies=(leaf.ref,),
    )
    for record in (*catalog.search(), root.ref.get(), *root.ref.dependencies()):
        assert record.ref._catalog is catalog
        assert record.ref.get() == record
    graph = Graph(
        catalog,
        local_record(Record(Ref("soc", "local", "rtl", "default", "1.0", catalog=catalog))),
        dependencies=(root.ref,),
    )
    records = {record.ref: record for record in graph.records}
    for graph_record in graph.records:
        assert graph_record.ref._catalog is catalog
    for edge in graph.dependencies:
        assert edge.source is records[edge.source].ref
        assert edge.target is records[edge.target].ref
        assert edge.target.get() == records[edge.target]


def test_json_decoding_creates_refs_with_explicit_context(catalog: Catalog) -> None:
    published = catalog.publish(
        local_record(Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)))
    )
    restored = Record.from_json(json.dumps(published.to_dict(), allow_nan=False), catalog=catalog)
    assert restored.ref.get() == published
    assert restored.ref._catalog is catalog


def test_reference_requires_a_private_catalog(catalog: Catalog) -> None:
    ref = Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)
    assert ref._catalog is catalog
    assert not hasattr(ref, "catalog")
    assert not is_dataclass(ref)
    assert tuple(name for name in vars(ref) if "catalog" in name) == ("_catalog",)
    with pytest.raises(AttributeError):
        ref.component = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        Ref("soc", "cpu", "rtl", "default", "1.0")  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="requires a catalog"):
        Ref("soc", "cpu", "rtl", "default", "1.0", catalog=None)  # type: ignore[arg-type]


def test_dependent_lookup_filters_candidates(catalog: Catalog) -> None:
    rtl = Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)
    catalog.publish(local_record(Record(rtl)))
    macro = catalog.publish(
        local_record(Record(Ref("soc", "cpu", "be", "lib-lef", "4.0", catalog=catalog))),
        dependencies=(rtl,),
    )
    query = SearchQuery(namespace="soc", component="cpu", type="lib-lef", view="be")
    assert rtl.find_dependents(candidates=query, transitive=False) == (macro,)
    assert rtl.find_dependents(candidates=SearchQuery(view="rtl"), transitive=False) == ()
