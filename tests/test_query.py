"""Name, version, kind and namespace participate in catalog search."""

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import Record, Ref
from tests.helpers import release, resolve_published, run


def test_criteria_are_detached() -> None:
    query = SearchQuery(component="", version=" ", view="", type="", namespace="")
    criteria = query.criteria()
    assert criteria == {"component": "", "version": " ", "view": "", "type": "", "namespace": ""}
    criteria.clear()
    assert query.criteria() == {
        "component": "",
        "version": " ",
        "view": "",
        "type": "",
        "namespace": "",
    }
    assert SearchQuery().criteria() == {}


@pytest.mark.parametrize(
    "query",
    [
        SearchQuery(),
        SearchQuery(component="rtl"),
        SearchQuery(version="v1"),
        SearchQuery(component="rtl", version="v1"),
        SearchQuery(view="release"),
        SearchQuery(view="run"),
        SearchQuery(namespace="demo-ip"),
        SearchQuery(namespace="flow"),
        SearchQuery(namespace="missing"),
        SearchQuery(namespace=""),
        SearchQuery(component="rtl", version="v1", view="release", namespace="demo-ip"),
        SearchQuery(component="rtl", namespace="flow"),
        SearchQuery(component="rtl", version="v1", view="run"),
        SearchQuery(view="unknown"),
        SearchQuery(view=""),
        SearchQuery(version="V1"),
        SearchQuery(component=""),
        SearchQuery(component="' OR true //"),
    ],
)
def test_query_matching_and_catalog_search_agree(catalog: Catalog, query: SearchQuery) -> None:
    records = (release("rtl"), run("synthesis"))
    for record in records:
        catalog.publish(record)
    expected = tuple(record for record in records if query.matches(record))
    assert catalog.search(query) == tuple(sorted(expected, key=lambda record: record.ref))


def test_payload_cannot_override_identity(catalog: Catalog) -> None:
    record = Record(
        Ref("demo", "source", "release", "default", "1.0"),
        {"name": "wrong", "version": "wrong", "kind": "payload-kind", "namespace": "payload-ns"},
    )
    catalog.publish(record)
    assert catalog.search(SearchQuery(component="source", version="1.0")) == (record,)
    assert not catalog.search(SearchQuery(component="wrong"))
    assert catalog.search(SearchQuery(view="release")) == (record,)
    assert not catalog.search(SearchQuery(view="payload-kind"))
    assert catalog.search(SearchQuery(namespace="demo")) == (record,)
    assert not catalog.search(SearchQuery(namespace="payload-ns"))


def test_namespaces_and_kinds_distinguish_equal_names(catalog: Catalog) -> None:
    records = tuple(
        Record(Ref(namespace, "core", kind, "default", "1.0"))
        for namespace in ("rtl", "physical")
        for kind in ("ip", "soc")
    )
    root = Record(Ref("demo", "top", "release", "default", "1.0"))
    for record in records:
        catalog.publish(record)
    catalog.publish(root, dependencies=tuple(record.ref for record in records))
    assert len(resolve_published(catalog, root.ref).records) == 5
    assert {
        record.ref for record in catalog.search(SearchQuery(component="core", version="1.0"))
    } == {record.ref for record in records}
    assert len(catalog.search(SearchQuery(namespace="rtl"))) == 2
    assert len(catalog.search(SearchQuery(component="core", view="soc", namespace="physical"))) == 1
    for record in records:
        assert Ref(**record.ref.to_dict(), catalog=catalog).get() == record


def test_kind_distinguishes_same_namespace_name_and_version(catalog: Catalog) -> None:
    refs = tuple(
        Ref("demo", "same", kind, "default", "1.0") for kind in ("release", "run", "report")
    )
    records = tuple(Record(ref) for ref in refs)
    root = Record(Ref("demo", "top", "package", "default", "1.0"))
    for record in records:
        catalog.publish(record)
    catalog.publish(root, dependencies=refs)
    assert len(set(refs)) == len(refs)
    assert len({ref.package_name for ref in refs}) == len(refs)
    assert len(catalog.search(SearchQuery(component="same", version="1.0"))) == 3
    assert len(resolve_published(catalog, root.ref).records) == 4
    for record in records:
        assert catalog.search(SearchQuery(view=record.ref.view)) == (record,)
        assert Ref(**record.ref.to_dict(), catalog=catalog).get() == record


def test_view_and_type_are_independent_identity_and_search_fields(catalog: Catalog) -> None:
    rtl = catalog.publish(Record(Ref("soc", "cpu", "rtl", "default", "1.0")))
    alternate = catalog.publish(Record(Ref("soc", "cpu", "rtl", "gate", "1.0")))
    be = catalog.publish(Record(Ref("soc", "cpu", "be", "lib-lef", "1.0")))
    assert len({rtl.ref, alternate.ref, be.ref}) == 3
    assert str(rtl.ref) == "soc.cpu.rtl.default/1.0"
    assert str(be.ref) == "soc.cpu.be.lib-lef/1.0"
    assert catalog.search(SearchQuery(view="rtl")) == (rtl, alternate)
    assert catalog.search(SearchQuery(type="gate")) == (alternate,)
    assert catalog.search(SearchQuery(view="be", type="lib-lef")) == (be,)
    assert catalog.search(SearchQuery(view="rtl", type="lib-lef")) == ()
