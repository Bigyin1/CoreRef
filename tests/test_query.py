"""Name, version, kind and namespace participate in catalog search."""

from __future__ import annotations

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import InvalidPackage
from coreref.api.query import SearchQuery
from coreref.api.records import Record, Ref
from tests.helpers import local_record, release, resolve_published, run


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
        SearchQuery(component="rtl", version="v1", view="release", namespace="demo-ip"),
        SearchQuery(component="rtl", namespace="flow"),
        SearchQuery(component="rtl", version="v1", view="run"),
        SearchQuery(view="unknown"),
    ],
)
def test_query_matching_and_catalog_search_agree(catalog: Catalog, query: SearchQuery) -> None:
    records: tuple[Record, ...] = (
        release("rtl"),
        run("synthesis"),
    )
    records = tuple(catalog.publish(local_record(record)) for record in records)
    expected = tuple(record for record in records if query.matches(record))
    assert catalog.search(query) == tuple(sorted(expected, key=lambda record: record.ref))


def test_payload_cannot_override_identity(catalog: Catalog) -> None:
    record = Record(
        Ref.from_fields("demo", "source", "release", "default", "1.0"),
        {"name": "wrong", "version": "wrong", "kind": "payload-kind", "namespace": "payload-ns"},
    )
    record = catalog.publish(local_record(record))
    assert catalog.search(SearchQuery(component="source", version="1.0")) == (record,)
    assert not catalog.search(SearchQuery(component="wrong"))
    assert catalog.search(SearchQuery(view="release")) == (record,)
    assert not catalog.search(SearchQuery(view="payload-kind"))
    assert catalog.search(SearchQuery(namespace="demo")) == (record,)
    assert not catalog.search(SearchQuery(namespace="payload-ns"))


def test_namespaces_and_kinds_distinguish_equal_names(catalog: Catalog) -> None:
    records = tuple(
        Record(Ref.from_fields(namespace, "core", kind, "default", "1.0"))
        for namespace in ("rtl", "physical")
        for kind in ("ip", "soc")
    )
    root = Record(Ref.from_fields("demo", "top", "release", "default", "1.0"))
    records = tuple(catalog.publish(local_record(record)) for record in records)
    root = catalog.publish(local_record(root), dependencies=tuple(record.ref for record in records))
    assert len(resolve_published(catalog, root.ref).records) == 5
    assert {
        record.ref for record in catalog.search(SearchQuery(component="core", version="1.0"))
    } == {record.ref for record in records}
    assert len(catalog.search(SearchQuery(namespace="rtl"))) == 2
    assert len(catalog.search(SearchQuery(component="core", view="soc", namespace="physical"))) == 1
    for record in records:
        assert catalog.get(Ref.from_fields(**record.ref.to_dict())) == record


def test_kind_distinguishes_same_namespace_name_and_version(catalog: Catalog) -> None:
    refs = tuple(
        Ref.from_fields("demo", "same", kind, "default", "1.0")
        for kind in ("release", "run", "report")
    )
    records = tuple(Record(ref) for ref in refs)
    root = Record(Ref.from_fields("demo", "top", "package", "default", "1.0"))
    records = tuple(catalog.publish(local_record(record)) for record in records)
    root = catalog.publish(local_record(root), dependencies=refs)
    assert len(set(refs)) == len(refs)
    assert len({ref.package_name for ref in refs}) == len(refs)
    assert len(catalog.search(SearchQuery(component="same", version="1.0"))) == 3
    assert len(resolve_published(catalog, root.ref).records) == 4
    for record in records:
        assert catalog.search(SearchQuery(view=record.ref.view)) == (record,)
        assert catalog.get(Ref.from_fields(**record.ref.to_dict())) == record


def test_view_and_type_are_independent_identity_and_search_fields(catalog: Catalog) -> None:
    rtl = catalog.publish(
        local_record(Record(Ref.from_fields("soc", "cpu", "rtl", "default", "1.0")))
    )
    alternate = catalog.publish(
        local_record(Record(Ref.from_fields("soc", "cpu", "rtl", "gate", "1.0")))
    )
    be = catalog.publish(
        local_record(Record(Ref.from_fields("soc", "cpu", "be", "lib-lef", "1.0")))
    )
    assert len({rtl.ref, alternate.ref, be.ref}) == 3
    assert str(rtl.ref.without_revision()) == "soc.cpu.rtl.default/1.0"
    assert str(be.ref.without_revision()) == "soc.cpu.be.lib-lef/1.0"
    assert catalog.search(SearchQuery(view="rtl")) == (rtl, alternate)
    assert catalog.search(SearchQuery(type="gate")) == (alternate,)
    assert catalog.search(SearchQuery(view="be", type="lib-lef")) == (be,)
    assert catalog.search(SearchQuery(view="rtl", type="lib-lef")) == ()


@pytest.mark.parametrize(
    "query, pattern",
    [
        (SearchQuery(), "*.*.*.*/*#*"),
        (
            SearchQuery(namespace="soc", component="cpu", view="be", type="lib-lef"),
            "soc.cpu*.be.lib-lef/*#*",
        ),
        (SearchQuery(version="1.0"), "*.*.*.*/1.0#*"),
    ],
)
def test_pattern_preserves_exact_filters(query: SearchQuery, pattern: str) -> None:
    assert query._conan_pattern(all_revisions=True) == pattern


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", ""),
        ("namespace", "with/slash"),
        ("view", "RTL"),
        ("view", ""),
        ("component", "cpu*"),
        ("component", "' OR true //"),
        ("type", "?"),
        ("version", "[>=1.0]"),
        ("version", "V1"),
        ("subcomponent", ""),
        ("subcomponent", "alu*"),
        ("revision", "*"),
    ],
)
def test_invalid_search_fields_raise_domain_errors(field: str, value: str) -> None:
    with pytest.raises(InvalidPackage, match=field):
        SearchQuery(**{field: value})
