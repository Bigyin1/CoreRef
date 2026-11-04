"""Candidate searches preserve exact references across arbitrary dependency paths."""

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.enums import RelationKind
from hwcatalog.api.errors import NotFound
from hwcatalog.api.models.graph import Relation
from hwcatalog.api.models.query import SearchQuery
from hwcatalog.api.models.records import Record, Ref


def test_candidate_search_uses_arbitrary_paths_and_exact_versions(catalog: Catalog) -> None:
    rtl = Record(Ref("cpu", "1.0", "rtl", "soc"))
    other = Record(Ref("cpu", "2.0", "rtl", "soc"))
    middle = Record(Ref("unrelated-component", "3.0", "anything", "tools"))
    macro = Record(Ref("cpu", "4.0", "lib-lef", "soc"))
    wrong = Record(Ref("cpu", "5.0", "lib-lef", "soc"))
    unrelated = Record(Ref("cpu", "6.0", "lib-lef", "soc"))
    edges = (
        Relation(middle.ref, rtl.ref, RelationKind.REQUIRES),
        Relation(macro.ref, middle.ref, RelationKind.REQUIRES),
        Relation(wrong.ref, other.ref, RelationKind.REQUIRES),
    )
    catalog.publish((rtl, other, middle, macro, wrong, unrelated), relations=edges)
    query = SearchQuery(namespace="soc", name="cpu", kind="lib-lef")
    assert catalog.find_dependents(rtl.ref, candidates=query) == (macro,)
    assert not catalog.find_dependents(rtl.ref, candidates=query, transitive=False)
    assert catalog.find_dependents(rtl.ref, candidates=SearchQuery(), transitive=False) == (middle,)
    assert catalog.find_dependents(rtl.ref, candidates=SearchQuery(kind="rtl")) == ()
    assert catalog.find_dependents(other.ref, candidates=query) == (wrong,)
    with pytest.raises(NotFound):
        catalog.find_dependents(Ref("missing", "1.0", "rtl", "soc"), candidates=query)


def test_exact_closure_is_the_default(catalog: Catalog) -> None:
    root, first, second = (
        Record(Ref(name, version, "rtl", "soc"))
        for name, version in (("top", "1.0"), ("cpu", "1.0"), ("cpu", "2.0"))
    )
    catalog.publish(
        (root, first, second),
        relations=(
            Relation(root.ref, first.ref, RelationKind.REQUIRES),
            Relation(root.ref, second.ref, RelationKind.REQUIRES),
        ),
    )
    assert {record.ref for record in catalog.graph.dependency_closure(root.ref).records} == {
        root.ref,
        first.ref,
        second.ref,
    }
