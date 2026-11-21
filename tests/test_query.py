"""Search exact coordinates independently of stored payloads."""

from __future__ import annotations

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import InvalidPackage
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Ref


@pytest.mark.parametrize(
    "query",
    [
        SearchQuery(),
        SearchQuery(component="core"),
        SearchQuery(namespace="rtl"),
        SearchQuery(namespace="physical", component="core", version="1.0"),
        SearchQuery(component="core", version="1.0"),
        SearchQuery(namespace="missing"),
    ],
)
def test_query_matching_and_catalog_search_agree(catalog: Catalog, query: SearchQuery) -> None:
    records = tuple(
        catalog.publish(LocalRecord(Ref(ref), payload={"namespace": "missing"}))
        for ref in ("rtl.core/1.0", "physical.core/1.0", "rtl.core/2.0", "rtl.core-extra/1.0")
    )
    expected = tuple(record for record in records if query.matches(record))
    assert catalog.search(query) == tuple(sorted(expected, key=lambda record: record.ref))


@pytest.mark.parametrize(
    "query,pattern",
    [
        (SearchQuery(), "*.*/*#*"),
        (SearchQuery(namespace="soc", component="cpu"), "soc.cpu/*#*"),
        (SearchQuery(version="1.0"), "*.*/1.0#*"),
    ],
)
def test_search_patterns(query: SearchQuery, pattern: str) -> None:
    assert query._conan_pattern(all_revisions=True) == pattern


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", ""),
        ("namespace", "with/slash"),
        ("component", "cpu*"),
        ("component", "cpu.rtl"),
        ("version", "[>=1.0]"),
        ("version", "V1"),
        ("revision", "*"),
    ],
)
def test_invalid_search_fields_raise_domain_errors(field: str, value: str) -> None:
    with pytest.raises(InvalidPackage, match=field):
        SearchQuery(**{field: value})
