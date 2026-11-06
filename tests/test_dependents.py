"""Candidate searches preserve exact references across arbitrary dependency paths."""

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError, NotFound
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import Record, Ref


def test_candidate_search_uses_arbitrary_paths_and_exact_versions(catalog: Catalog) -> None:
    rtl = Record(Ref("soc", "cpu", "rtl", "default", "1.0"))
    other = Record(Ref("soc", "cpu", "rtl", "default", "2.0"))
    middle = Record(Ref("tools", "unrelated-component", "anything", "default", "3.0"))
    macro = Record(Ref("soc", "cpu", "be", "lib-lef", "4.0"))
    wrong = Record(Ref("soc", "cpu", "be", "lib-lef", "5.0"))
    unrelated = Record(Ref("soc", "cpu", "be", "lib-lef", "6.0"))
    catalog.publish(rtl)
    catalog.publish(other)
    catalog.publish(middle, dependencies=(rtl.ref,))
    catalog.publish(macro, dependencies=(middle.ref,))
    catalog.publish(wrong, dependencies=(other.ref,))
    catalog.publish(unrelated)
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
    first = Record(Ref("soc", "cpu", "rtl", "default", "1.0"))
    second = Record(Ref("soc", "cpu", "rtl", "default", "2.0"))
    catalog.publish(first)
    catalog.publish(second)
    with pytest.raises(CatalogError, match="Duplicated requirement"):
        catalog.publish(
            Record(Ref("soc", "top", "rtl", "default", "1.0")), dependencies=(first.ref, second.ref)
        )
    assert Ref(**first.ref.to_dict(), catalog=catalog).get() == first
    assert Ref(**second.ref.to_dict(), catalog=catalog).get() == second
