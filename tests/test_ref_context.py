"""References carry operation context without changing stored package identity."""

from pathlib import Path

import pytest

from examples.conan_config import configured_home
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError
from hwcatalog.api.graph import Graph
from hwcatalog.api.protocols import RefCatalog
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import Record, Ref


def test_same_coordinates_use_their_own_catalog(catalog: Catalog, tmp_path: Path) -> None:
    other = Catalog(configured_home(tmp_path / "other-home"))
    first = Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)
    second = Ref("soc", "cpu", "rtl", "default", "1.0", catalog=other)
    catalog.publish(Record(first, {"origin": "first"}))
    other.publish(Record(second, {"origin": "second"}))
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
    assert Record(first).to_json() == Record(second).to_json()
    assert first.get().payload == {"origin": "first"}
    assert second.get().payload == {"origin": "second"}
    assert first.get().ref.catalog is catalog
    assert second.get().ref.catalog is other


def test_catalog_creates_operational_refs_in_records_and_graphs(catalog: Catalog) -> None:
    leaf = catalog.publish(Record(Ref("soc", "leaf", "rtl", "default", "1.0")))
    root = catalog.publish(
        Record(Ref("soc", "root", "rtl", "default", "1.0")), dependencies=(leaf.ref,)
    )
    for record in (*catalog.search(), root.ref.get(), *root.ref.dependencies()):
        assert record.ref.catalog is catalog
        assert record.ref.get() == record
    graph = Graph(
        catalog, Record(Ref("soc", "local", "rtl", "default", "1.0")), dependencies=(root.ref,)
    ).resolve()
    records = {record.ref: record for record in graph.records}
    for record in graph.records:
        assert record.ref.catalog is catalog
    for edge in graph.dependencies:
        assert edge.source is records[edge.source].ref
        assert edge.target is records[edge.target].ref
        assert edge.target.get() == records[edge.target]


def test_json_decoding_creates_refs_with_explicit_context(catalog: Catalog) -> None:
    published = catalog.publish(Record(Ref("soc", "cpu", "rtl", "default", "1.0")))
    restored = Record.from_json(published.to_json(), catalog=catalog)
    assert restored.ref.get() == published
    assert restored.ref.catalog is catalog
    assert Record.from_json(published.to_json()).ref.catalog is None


@pytest.mark.parametrize("operation", ["get", "dependencies", "materialize", "find_dependents"])
def test_unbound_ref_reports_missing_context(operation: str) -> None:
    ref = Ref("soc", "cpu", "rtl", "default", "1.0")
    with pytest.raises(CatalogError, match="pass catalog="):
        if operation == "find_dependents":
            ref.find_dependents(candidates=SearchQuery())
        else:
            getattr(ref, operation)()


def test_dependent_lookup_uses_protocol_search(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    context: RefCatalog = catalog
    rtl = Ref("soc", "cpu", "rtl", "default", "1.0", catalog=context)
    catalog.publish(Record(rtl))
    macro = catalog.publish(Record(Ref("soc", "cpu", "be", "lib-lef", "4.0")), dependencies=(rtl,))
    queries: list[SearchQuery] = []
    search = catalog.search

    def track(query: SearchQuery) -> tuple[Record, ...]:
        queries.append(query)
        return search(query)

    monkeypatch.setattr(catalog, "search", track)
    query = SearchQuery(namespace="soc", component="cpu", type="lib-lef", view="be")
    assert rtl.find_dependents(candidates=query, transitive=False) == (macro,)
    assert queries == [query]
