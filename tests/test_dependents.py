"""Candidate searches preserve exact references across arbitrary dependency paths."""

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import CatalogError, NotFound
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref


def test_candidate_search_uses_arbitrary_paths_and_exact_versions(catalog: Catalog) -> None:
    rtl = Record(Ref("cpu", "1.0", "rtl", "soc"))
    other = Record(Ref("cpu", "2.0", "rtl", "soc"))
    middle = Record(Ref("unrelated-component", "3.0", "anything", "tools"))
    macro = Record(Ref("cpu", "4.0", "lib-lef", "soc"))
    wrong = Record(Ref("cpu", "5.0", "lib-lef", "soc"))
    unrelated = Record(Ref("cpu", "6.0", "lib-lef", "soc"))
    catalog.publish(rtl)
    catalog.publish(other)
    catalog.publish(middle, dependencies=(rtl.ref,))
    catalog.publish(macro, dependencies=(middle.ref,))
    catalog.publish(wrong, dependencies=(other.ref,))
    catalog.publish(unrelated)
    query = SearchQuery(namespace="soc", name="cpu", kind="lib-lef")
    assert Ref(**rtl.ref.to_dict(), catalog=catalog).find_dependents(candidates=query) == (macro,)
    assert not Ref(**rtl.ref.to_dict(), catalog=catalog).find_dependents(
        candidates=query, transitive=False
    )
    assert Ref(**rtl.ref.to_dict(), catalog=catalog).find_dependents(
        candidates=SearchQuery(), transitive=False
    ) == (middle,)
    assert (
        Ref(**rtl.ref.to_dict(), catalog=catalog).find_dependents(
            candidates=SearchQuery(kind="rtl")
        )
        == ()
    )
    assert Ref(**other.ref.to_dict(), catalog=catalog).find_dependents(candidates=query) == (wrong,)
    with pytest.raises(NotFound):
        Ref("missing", "1.0", "rtl", "soc", catalog=catalog).find_dependents(candidates=query)


def test_conflicting_versions_are_rejected_by_conan(catalog: Catalog) -> None:
    first = Record(Ref("cpu", "1.0", "rtl", "soc"))
    second = Record(Ref("cpu", "2.0", "rtl", "soc"))
    catalog.publish(first)
    catalog.publish(second)
    with pytest.raises(CatalogError, match="Duplicated requirement"):
        catalog.publish(
            Record(Ref("top", "1.0", "rtl", "soc")), dependencies=(first.ref, second.ref)
        )
    assert Ref(**first.ref.to_dict(), catalog=catalog).get() == first
    assert Ref(**second.ref.to_dict(), catalog=catalog).get() == second
