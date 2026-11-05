"""Name, version, kind and namespace participate in catalog search."""

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref
from tests.helpers import release, resolve_published, run


def test_criteria_are_detached() -> None:
    query = SearchQuery(name="", version=" ", kind="", namespace="")
    criteria = query.criteria()
    assert criteria == {"name": "", "version": " ", "kind": "", "namespace": ""}
    criteria.clear()
    assert query.criteria() == {"name": "", "version": " ", "kind": "", "namespace": ""}
    assert SearchQuery().criteria() == {}


@pytest.mark.parametrize(
    "query",
    [
        SearchQuery(),
        SearchQuery(name="rtl"),
        SearchQuery(version="v1"),
        SearchQuery(name="rtl", version="v1"),
        SearchQuery(kind="release"),
        SearchQuery(kind="run"),
        SearchQuery(namespace="demo-ip"),
        SearchQuery(namespace="flow"),
        SearchQuery(namespace="missing"),
        SearchQuery(namespace=""),
        SearchQuery(name="rtl", version="v1", kind="release", namespace="demo-ip"),
        SearchQuery(name="rtl", namespace="flow"),
        SearchQuery(name="rtl", version="v1", kind="run"),
        SearchQuery(kind="unknown"),
        SearchQuery(kind=""),
        SearchQuery(version="V1"),
        SearchQuery(name=""),
        SearchQuery(name="' OR true //"),
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
        Ref("source", "1.0", "release", "demo"),
        {"name": "wrong", "version": "wrong", "kind": "payload-kind", "namespace": "payload-ns"},
    )
    catalog.publish(record)
    assert catalog.search(SearchQuery(name="source", version="1.0")) == (record,)
    assert not catalog.search(SearchQuery(name="wrong"))
    assert catalog.search(SearchQuery(kind="release")) == (record,)
    assert not catalog.search(SearchQuery(kind="payload-kind"))
    assert catalog.search(SearchQuery(namespace="demo")) == (record,)
    assert not catalog.search(SearchQuery(namespace="payload-ns"))


def test_namespaces_and_kinds_distinguish_equal_names(catalog: Catalog) -> None:
    records = tuple(
        Record(Ref("core", "1.0", kind, namespace))
        for namespace in ("rtl", "physical")
        for kind in ("ip", "soc")
    )
    root = Record(Ref("top", "1.0", "release", "demo"))
    for record in records:
        catalog.publish(record)
    catalog.publish(root, dependencies=tuple(record.ref for record in records))
    assert len(resolve_published(catalog, root.ref).records) == 5
    assert {record.ref for record in catalog.search(SearchQuery(name="core", version="1.0"))} == {
        record.ref for record in records
    }
    assert len(catalog.search(SearchQuery(namespace="rtl"))) == 2
    assert len(catalog.search(SearchQuery(name="core", kind="soc", namespace="physical"))) == 1
    for record in records:
        assert catalog.get(record.ref) == record


def test_kind_distinguishes_same_namespace_name_and_version(catalog: Catalog) -> None:
    refs = tuple(Ref("same", "1.0", kind, "demo") for kind in ("release", "run", "report"))
    records = tuple(Record(ref) for ref in refs)
    root = Record(Ref("top", "1.0", "package", "demo"))
    for record in records:
        catalog.publish(record)
    catalog.publish(root, dependencies=refs)
    assert len(set(refs)) == len(refs)
    assert len({ref.package_name for ref in refs}) == len(refs)
    assert len(catalog.search(SearchQuery(name="same", version="1.0"))) == 3
    assert len(resolve_published(catalog, root.ref).records) == 4
    for record in records:
        assert catalog.search(SearchQuery(kind=record.ref.kind)) == (record,)
        assert catalog.get(record.ref) == record
