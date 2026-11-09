"""Representation queries preserve component identity, with independent versions and revisions."""

from __future__ import annotations

import pytest

from coreref.api.catalog import Catalog
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Ref


@pytest.mark.parametrize("subcomponent", [None, "alu"])
def test_component_query_does_not_inherit_rtl_version_or_revision(
    catalog: Catalog, subcomponent: str | None
) -> None:
    rtl = Ref.from_fields(
        "soc",
        "cpu",
        "rtl",
        "default",
        "1.0",
        revision="a" * 32,
        subcomponent=subcomponent,
    )
    be = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "cpu", "be", "lib-lef", "4.0", subcomponent=subcomponent)
        )
    )
    catalog.publish(
        LocalRecord(
            Ref.from_fields("other", "cpu", "be", "lib-lef", "4.0", subcomponent=subcomponent)
        )
    )
    query = SearchQuery.for_component(rtl, view="be", type="lib-lef")
    assert query.version is None and query.revision is None
    assert query.subcomponent == subcomponent
    assert catalog.search(query) == (be,)
    exact = SearchQuery.for_component(
        rtl, view="be", type="lib-lef", version="4.0", revision=be.ref.revision
    )
    assert catalog.search(exact) == (be,)
    assert query.matches_ref(be.ref) and not query.matches_ref(rtl)
