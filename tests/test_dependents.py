"""Candidate searches preserve exact references across arbitrary dependency paths."""

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError, NotFound
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import Record, Ref
from tests.helpers import local_record


def test_candidate_search_uses_arbitrary_paths_and_exact_versions(catalog: Catalog) -> None:
    rtl = Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
    other = Record(Ref("soc", "cpu", "rtl", "default", "2.0", catalog=catalog))
    middle = Record(
        Ref("tools", "unrelated-component", "anything", "default", "3.0", catalog=catalog)
    )
    macro = Record(Ref("soc", "cpu", "be", "lib-lef", "4.0", catalog=catalog))
    wrong = Record(Ref("soc", "cpu", "be", "lib-lef", "5.0", catalog=catalog))
    unrelated = Record(Ref("soc", "cpu", "be", "lib-lef", "6.0", catalog=catalog))
    catalog.publish(local_record(rtl))
    catalog.publish(local_record(other))
    catalog.publish(local_record(middle), dependencies=(rtl.ref,))
    catalog.publish(local_record(macro), dependencies=(middle.ref,))
    catalog.publish(local_record(wrong), dependencies=(other.ref,))
    catalog.publish(local_record(unrelated))
    query = SearchQuery(namespace="soc", component="cpu", type="lib-lef", view="be")
    assert Ref(**rtl.ref.to_dict(), catalog=catalog).find_dependents(candidates=query) == (macro,)
    assert not Ref(**rtl.ref.to_dict(), catalog=catalog).find_dependents(
        candidates=query, transitive=False
    )
    assert Ref(**rtl.ref.to_dict(), catalog=catalog).find_dependents(
        candidates=SearchQuery(), transitive=False
    ) == (middle,)
    assert (
        Ref(**rtl.ref.to_dict(), catalog=catalog).find_dependents(
            candidates=SearchQuery(view="rtl")
        )
        == ()
    )
    assert Ref(**other.ref.to_dict(), catalog=catalog).find_dependents(candidates=query) == (wrong,)
    with pytest.raises(NotFound):
        Ref("soc", "missing", "rtl", "default", "1.0", catalog=catalog).find_dependents(
            candidates=query
        )


def test_conflicting_versions_are_rejected_by_conan(catalog: Catalog) -> None:
    first = Record(Ref("soc", "cpu", "rtl", "default", "1.0", catalog=catalog))
    second = Record(Ref("soc", "cpu", "rtl", "default", "2.0", catalog=catalog))
    catalog.publish(local_record(first))
    catalog.publish(local_record(second))
    with pytest.raises(CatalogError, match="Duplicated requirement"):
        catalog.publish(
            local_record(Record(Ref("soc", "top", "rtl", "default", "1.0", catalog=catalog))),
            dependencies=(first.ref, second.ref),
        )
    assert Ref(**first.ref.to_dict(), catalog=catalog).get() == first
    assert Ref(**second.ref.to_dict(), catalog=catalog).get() == second
