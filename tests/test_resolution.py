"""Compatible version resolution and its stability, shared by both storage backends."""

from dataclasses import replace

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.enums import Direction, LineageDirection, RelationKind
from hwcatalog.api.errors import DependencyCycle, InvalidPublication, VersionConflict
from hwcatalog.api.models.graph import Relation
from hwcatalog.api.models.records import Record, Ref
from tests.helpers import (
    graph_json,
    greedy_resolution_graph,
    release,
    resolution_graph,
    resolve_in_process,
    run,
)


def _requires(source: Record, target: Record) -> Relation:
    return Relation(source.ref, target.ref, RelationKind.REQUIRES)


def _compatible(new: Record, old: Record) -> Relation:
    return Relation(new.ref, old.ref, RelationKind.BACKWARD_COMPATIBLE_WITH)


def test_transitive_resolution_retains_selected_record_and_bindings(catalog: Catalog) -> None:
    root, records, relations = resolution_graph()
    catalog.publish(records, relations=relations)
    stored = tuple(catalog.get(record.ref) for record in records)
    result = catalog.graph.dependency_closure(root.ref, resolve_versions=True)
    selected = release("resolver-lib", "a-new").ref
    assert {record.ref for record in result.records} == {root.ref, selected, release("current").ref}
    assert catalog.get(selected) in result.records
    assert next(record for record in result.records if record.ref == selected) == records[3]
    root_edges = tuple(edge for edge in result.relations if edge.source == root.ref)
    assert len(root_edges) == 2
    assert {edge.target for edge in root_edges} == {selected}
    assert {(edge.alias, edge.ordinal) for edge in root_edges} == {("input", 0), ("input", 1)}
    assert {edge.metadata["ports"] for edge in root_edges} == {(1,), (2,)}
    assert all(edge.kind is RelationKind.REQUIRES for edge in result.relations)
    assert len(result.to_networkx().edges) == 3
    assert tuple(catalog.get(record.ref) for record in records) == stored
    raw = catalog.graph.dependency_closure(root.ref, resolve_versions=False)
    assert {record.ref for record in raw.records} == {record.ref for record in records} - {
        release("resolver-lib", "bridge").ref,
    }
    assert {neighbor.relation.target for neighbor in catalog.graph.dependencies(root.ref)} == {
        release("resolver-lib", "z-old").ref,
        selected,
    }
    proof = catalog.graph.neighbors(selected, RelationKind.BACKWARD_COMPATIBLE_WITH)
    assert proof[0].record.ref == release("resolver-lib", "bridge").ref
    assert Relation.from_json(proof[0].relation.to_json()) == proof[0].relation
    assert catalog.graph.lineage(selected, LineageDirection.UPSTREAM).relations == ()
    with pytest.raises(TypeError):
        root_edges[0].metadata["changed"] = True  # type: ignore[index]


def test_resolution_deduplicates_only_identical_bindings(catalog: Catalog) -> None:
    root, old, new = release("root"), release("lib", "old"), release("lib", "new")
    edges = (_requires(root, old), _requires(root, new), _compatible(new, old))
    catalog.publish((root, old, new), relations=edges)
    result = catalog.graph.dependency_closure(root.ref, resolve_versions=True)
    assert result.relations == (_requires(root, new),)
    assert len(catalog.graph.dependencies(root.ref)) == 2


def test_namespaces_are_independent(catalog: Catalog) -> None:
    root, first = release("root"), release("lib", "one")
    second = release("lib", "two")
    second = replace(second, ref=replace(second.ref, namespace="other"))
    catalog.publish(
        (root, first, second), relations=(_requires(root, first), _requires(root, second))
    )
    assert len(catalog.graph.dependency_closure(root.ref, resolve_versions=True).records) == 3


@pytest.mark.parametrize("multiple_passes", [False, True])
def test_discarded_branches_do_not_cause_version_conflicts(
    catalog: Catalog,
    multiple_passes: bool,
) -> None:
    root, a1, a2 = release("root"), release("a", "1"), release("a", "2")
    b1, b2, b3 = release("b", "1"), release("b", "2"), release("b", "3")
    edges = [_requires(root, a1), _requires(root, a2), _compatible(a2, a1), _requires(a2, b2)]
    if multiple_passes:
        edges.extend((_requires(a1, b3), _requires(root, b1), _compatible(b2, b1)))
    else:
        edges.append(_requires(a1, b1))
    catalog.publish((root, a1, a2, b1, b2, b3), relations=tuple(edges))
    result = catalog.graph.dependency_closure(root.ref, resolve_versions=True)
    assert {record.ref for record in result.records} == {root.ref, a2.ref, b2.ref}


@pytest.mark.parametrize("parent_first", [False, True])
@pytest.mark.parametrize("coordinate", ["namespace", "name", "kind"])
def test_equal_depth_families_follow_coordinate_order(
    catalog: Catalog, parent_first: bool, coordinate: str
) -> None:
    parent_value, child_value = ("a", "z") if parent_first else ("z", "a")
    refs = tuple(Ref("package", version, "package", "example") for version in ("1", "2"))
    a1, a2 = (Record(replace(ref, **{coordinate: parent_value})) for ref in refs)
    b1, b2 = (Record(replace(ref, **{coordinate: child_value})) for ref in refs)
    root = release("root")
    edges = (
        _requires(root, a1),
        _requires(root, a2),
        _requires(root, b1),
        _requires(a1, b2),
        _compatible(a2, a1),
        _compatible(b2, b1),
    )
    catalog.publish((root, a1, a2, b1, b2), relations=edges)
    result = catalog.graph.dependency_closure(root.ref, resolve_versions=True)
    selected = b1 if parent_first else b2
    assert {record.ref for record in result.records} == {root.ref, a2.ref, selected.ref}
    assert _requires(root, selected) in result.relations


def test_nearest_family_is_resolved_before_an_obsolete_child(catalog: Catalog) -> None:
    root, records, relations = greedy_resolution_graph()
    catalog.publish(records, relations=relations)
    exact = graph_json(catalog.graph.dependency_closure(root.ref, resolve_versions=False))
    result = catalog.graph.dependency_closure(root.ref, resolve_versions=True)
    assert {record.ref for record in result.records} == {
        root.ref,
        release("z-parent", "new").ref,
        release("a-child", "old").ref,
        release("leaf", "old").ref,
    }
    assert len(result.relations) == 3
    assert graph_json(catalog.graph.dependency_closure(root.ref, resolve_versions=False)) == exact


def test_distances_are_recomputed_after_pruning(catalog: Catalog) -> None:
    root, a1, a2 = release("root"), release("a", "1"), release("a", "2")
    b1, b2 = release("b", "1"), release("b", "2")
    c1, c2 = release("c", "1"), release("c", "2")
    bridge, tail = release("bridge"), release("tail")
    records = (root, a1, a2, b1, b2, c1, c2, bridge, tail)
    edges = (
        _requires(root, a1),
        _requires(root, a2),
        _requires(a1, b1),
        _requires(a2, bridge),
        _requires(bridge, c1),
        _requires(bridge, c2),
        _requires(bridge, tail),
        _requires(tail, b1),
        _requires(c1, b2),
        _compatible(a2, a1),
        _compatible(b2, b1),
        _compatible(c2, c1),
    )
    catalog.publish(records, relations=edges)
    result = catalog.graph.dependency_closure(root.ref, resolve_versions=True)
    assert {record.ref for record in result.records} == {
        root.ref,
        a2.ref,
        b1.ref,
        c2.ref,
        bridge.ref,
        tail.ref,
    }


@pytest.mark.parametrize("seed", ["1", "73"])
def test_greedy_conflict_does_not_backtrack(catalog: Catalog, seed: str) -> None:
    """Cover the no-backtracking limitation discussed in the Conan FAQ, without ranges."""
    root, records, relations = greedy_resolution_graph()
    direct_child = _requires(root, release("a-child", "old"))
    catalog.publish(records, relations=(*relations, direct_child))
    with pytest.raises(VersionConflict, match="Incompatible versions for leaf") as failure:
        catalog.graph.dependency_closure(root.ref, resolve_versions=True)
    assert len(catalog.graph.dependency_closure(root.ref, resolve_versions=False).records) == 7
    assert (
        resolve_in_process(
            root.ref,
            records=records[::-1],
            relations=(*relations, direct_child)[::-1],
            hash_seed=seed,
        )
        == f"VersionConflict: {failure.value}"
    )


def test_greedy_resolution_is_stable_after_catalog_extension(catalog: Catalog) -> None:
    root, records, relations = greedy_resolution_graph()
    catalog.publish(records, relations=relations)
    expected = graph_json(catalog.graph.dependency_closure(root.ref, resolve_versions=True))
    future_parent = release("z-parent", "future")
    future_child = release("a-child", "future")
    fork, consumer = release("z-parent", "fork"), release("consumer")
    catalog.publish(
        (future_parent, future_child, fork, consumer),
        relations=(
            _compatible(future_parent, release("z-parent", "new")),
            _compatible(future_child, release("a-child", "new")),
            _compatible(fork, release("z-parent", "old")),
            _requires(consumer, root),
        ),
    )
    assert graph_json(catalog.graph.dependency_closure(root.ref, resolve_versions=True)) == expected


@pytest.mark.parametrize("obsolete_branch", [False, True])
def test_root_is_pinned_but_obsolete_root_requirements_are_discarded(
    catalog: Catalog,
    obsolete_branch: bool,
) -> None:
    root, newer = release("root", "1"), release("root", "2")
    a1, a2 = release("a", "1"), release("a", "2")
    edges = [_requires(root, a1), _requires(a1, newer), _compatible(newer, root)]
    if obsolete_branch:
        edges.extend((_requires(root, a2), _compatible(a2, a1)))
    catalog.publish((root, newer, a1, a2), relations=tuple(edges))
    if obsolete_branch:
        assert {
            r.ref for r in catalog.graph.dependency_closure(root.ref, resolve_versions=True).records
        } == {
            root.ref,
            a2.ref,
        }
    else:
        with pytest.raises(VersionConflict, match=r"Root release is pinned.*root"):
            catalog.graph.dependency_closure(root.ref, resolve_versions=True)
        assert len(catalog.graph.dependency_closure(root.ref, resolve_versions=False).records) == 3


@pytest.mark.parametrize("self_loop", [False, True])
def test_selected_dependencies_cannot_create_a_cycle(catalog: Catalog, self_loop: bool) -> None:
    records: tuple[Record, ...]
    edges: tuple[Relation, ...]
    if self_loop:
        root, previous = release("lib", "2"), release("lib", "1")
        records = (root, previous)
        edges = (_requires(root, previous), _compatible(root, previous))
    else:
        root, a = release("root"), release("a")
        b1, b2 = release("b", "1"), release("b", "2")
        records = (root, a, b1, b2)
        edges = (
            _requires(root, a),
            _requires(root, b2),
            _requires(a, b1),
            _requires(b2, a),
            _compatible(b2, b1),
        )
    catalog.publish(records, relations=edges)
    with pytest.raises(DependencyCycle, match="after resolving"):
        catalog.graph.dependency_closure(root.ref, resolve_versions=True)
    assert len(catalog.graph.dependency_closure(root.ref, resolve_versions=False).records) == len(
        records
    )


@pytest.mark.parametrize(
    "case",
    ["package", "namespace", "kind", "run", "multiple", "parallel", "cycle", "self"],
)
def test_invalid_compatibility_publications_are_atomic(catalog: Catalog, case: str) -> None:
    first, second, third = release("lib", "1"), release("lib", "2"), release("lib", "3")
    other = release("other")
    foreign = replace(
        third,
        ref=Ref(third.ref.name, third.ref.version, "release", "other"),
    )
    execution = run("run")
    other_kind = replace(first, ref=replace(first.ref, kind="run"))
    edges = {
        "package": (_compatible(second, other),),
        "namespace": (_compatible(second, foreign),),
        "kind": (_compatible(second, other_kind),),
        "run": (Relation(execution.ref, first.ref, RelationKind.BACKWARD_COMPATIBLE_WITH),),
        "multiple": (_compatible(third, first), _compatible(third, second)),
        "parallel": (
            _compatible(second, first),
            replace(_compatible(second, first), alias="other"),
        ),
        "cycle": (_compatible(second, first), _compatible(first, second)),
        "self": (_compatible(first, first),),
    }[case]
    with pytest.raises(InvalidPublication):
        catalog.publish(
            (
                first,
                second,
                third,
                other,
                foreign,
                execution,
                other_kind,
            ),
            relations=edges,
        )
    assert catalog.search() == ()


def test_compatibility_and_causal_cycles_are_independent(catalog: Catalog) -> None:
    first, second = release("lib", "1"), release("lib", "2")
    edges = (_requires(first, second), _compatible(second, first))
    catalog.publish((first, second), relations=edges)
    assert len(catalog.graph.dependency_closure(first.ref, resolve_versions=False).records) == 2


def test_cannot_add_compatibility_to_published_versions(catalog: Catalog) -> None:
    first, second, fresh = release("lib", "1"), release("lib", "2"), release("fresh")
    catalog.publish((first, second))
    with pytest.raises(InvalidPublication, match="outgoing relations"):
        catalog.publish((fresh,), relations=(_compatible(second, first),))
    assert catalog.graph.neighbors(second.ref, RelationKind.BACKWARD_COMPATIBLE_WITH) == ()
    assert {record.ref for record in catalog.search()} == {first.ref, second.ref}


def test_resolution_is_stable_after_all_kinds_of_catalog_extension(catalog: Catalog) -> None:
    root, records, relations = resolution_graph()
    catalog.publish(records, relations=relations)
    expected = graph_json(catalog.graph.dependency_closure(root.ref, resolve_versions=True))
    raw = graph_json(catalog.graph.dependency_closure(root.ref, resolve_versions=False))
    future, fork = release("resolver-lib", "future"), release("resolver-lib", "fork")
    future_root = release("resolver-root", "next")
    consumer, implementation, execution = release("consumer"), release("impl"), run("later")
    output, unused = release("output"), release("unused")
    extensions = (
        ((future,), (_compatible(future, release("resolver-lib", "a-new")),)),
        ((fork,), (_compatible(fork, release("resolver-lib", "z-old")),)),
        ((future_root,), (_compatible(future_root, root),)),
        ((consumer,), (_requires(consumer, root),)),
        (
            (implementation,),
            (Relation(implementation.ref, root.ref, RelationKind.IMPLEMENTS),),
        ),
        (
            (execution, output),
            (
                Relation(execution.ref, root.ref, RelationKind.USED),
                Relation(execution.ref, output.ref, RelationKind.PRODUCED),
            ),
        ),
        ((unused,), ()),
    )
    for new_records, new_relations in extensions:
        catalog.publish(new_records, relations=new_relations)
        assert (
            graph_json(catalog.graph.dependency_closure(root.ref, resolve_versions=True))
            == expected
        )
        assert graph_json(catalog.graph.dependency_closure(root.ref, resolve_versions=False)) == raw
    assert len(catalog.graph.consumers(root.ref)) == 1
    assert len(catalog.graph.implementations(root.ref)) == 1
    assert len(catalog.graph.neighbors(root.ref, RelationKind.USED, Direction.INCOMING)) == 1


def test_unresolved_forks_remain_conflicts_after_new_versions(catalog: Catalog) -> None:
    root, common = release("root"), release("lib", "base")
    left, right = release("lib", "left"), release("lib", "right")
    edges = (
        _requires(root, left),
        _requires(root, right),
        _compatible(left, common),
        _compatible(right, common),
    )
    catalog.publish((root, common, left, right), relations=edges)
    with pytest.raises(VersionConflict) as first:
        catalog.graph.dependency_closure(root.ref, resolve_versions=True)
    newer = release("lib", "newer")
    catalog.publish((newer,), relations=(_compatible(newer, left),))
    with pytest.raises(VersionConflict) as second:
        catalog.graph.dependency_closure(root.ref, resolve_versions=True)
    assert str(second.value) == str(first.value)


@pytest.mark.parametrize("seed", ["1", "73", "500"])
@pytest.mark.parametrize("greedy", [False, True])
def test_resolution_is_independent_of_process_hash_seed_and_input_order(
    catalog: Catalog,
    seed: str,
    greedy: bool,
) -> None:
    root, records, relations = greedy_resolution_graph() if greedy else resolution_graph()
    catalog.publish(records, relations=relations)
    expected = graph_json(catalog.graph.dependency_closure(root.ref, resolve_versions=True))
    assert (
        resolve_in_process(
            root.ref, records=records[::-1], relations=relations[::-1], hash_seed=seed
        )
        == expected
    )
