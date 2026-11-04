"""Conan documentation diamonds adapted to exact refs and explicit compatibility.

Sources:
https://docs.conan.io/2/tutorial/versioning/conflicts.html
https://docs.conan.io/2/tutorial/versioning/version_ranges.html
https://docs.conan.io/2/knowledge/faq.html

These tests do not emulate Conan ranges, overrides, force traits or binary selection.
"""

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.enums import RelationKind
from hwcatalog.api.errors import VersionConflict
from hwcatalog.api.models.graph import Relation
from hwcatalog.api.models.records import Record
from tests.helpers import graph_json, release


def _diamond(
    engine_version: str,
    intro_version: str,
    compatibility: tuple[tuple[str, str], ...],
    *,
    direct: str | None = None,
) -> tuple[Record, tuple[Record, ...], tuple[Relation, ...]]:
    game, engine, intro = release("game"), release("engine"), release("intro")
    matrix = {version: release("matrix", version) for version in ("1.0", "1.1", "1.2")}
    relations: tuple[Relation, ...] = (
        Relation(game.ref, engine.ref, RelationKind.REQUIRES),
        Relation(game.ref, intro.ref, RelationKind.REQUIRES),
        Relation(engine.ref, matrix[engine_version].ref, RelationKind.REQUIRES),
        Relation(intro.ref, matrix[intro_version].ref, RelationKind.REQUIRES),
    )
    relations += tuple(
        Relation(matrix[new].ref, matrix[old].ref, RelationKind.BACKWARD_COMPATIBLE_WITH)
        for new, old in compatibility
    )
    if direct is not None:
        relations += (Relation(game.ref, matrix[direct].ref, RelationKind.REQUIRES),)
    return game, (game, engine, intro, *matrix.values()), relations


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reversed"])
@pytest.mark.parametrize(
    ("engine_version", "intro_version", "compatibility", "selected"),
    [
        pytest.param("1.0", "1.0", (), "1.0", id="shared-exact-version"),
        pytest.param(
            "1.0", "1.0", (("1.1", "1.0"),), "1.0", id="unused-compatible-version-is-ignored"
        ),
        pytest.param("1.0", "1.1", (), None, id="different-versions-without-proof"),
        pytest.param("1.0", "1.1", (("1.1", "1.0"),), "1.1", id="declared-compatible-upgrade"),
        pytest.param("1.1", "1.0", (("1.1", "1.0"),), "1.1", id="swapped-branches"),
        pytest.param(
            "1.0", "1.1", (("1.2", "1.1"),), None, id="unrelated-proof-does-not-resolve-conflict"
        ),
    ],
)
def test_documented_diamond_cases(
    catalog: Catalog,
    reverse: bool,
    engine_version: str,
    intro_version: str,
    compatibility: tuple[tuple[str, str], ...],
    selected: str | None,
) -> None:
    game, records, relations = _diamond(engine_version, intro_version, compatibility)
    catalog.publish(
        records[::-1] if reverse else records,
        relations=relations[::-1] if reverse else relations,
    )
    exact = graph_json(catalog.graph.dependency_closure(game.ref, resolve_versions=False))
    if selected is None:
        with pytest.raises(VersionConflict, match="Incompatible versions for matrix"):
            catalog.graph.dependency_closure(game.ref, resolve_versions=True)
    else:
        result = catalog.graph.dependency_closure(game.ref, resolve_versions=True)
        assert {record.ref for record in result.records} == {
            game.ref,
            release("engine").ref,
            release("intro").ref,
            release("matrix", selected).ref,
        }
        incoming = tuple(edge for edge in result.relations if edge.target.name == "matrix")
        assert {edge.source for edge in incoming} == {release("engine").ref, release("intro").ref}
        assert {edge.target for edge in incoming} == {release("matrix", selected).ref}
        assert all(edge.kind is RelationKind.REQUIRES for edge in result.relations)
    assert graph_json(catalog.graph.dependency_closure(game.ref, resolve_versions=False)) == exact


@pytest.mark.parametrize("reverse", [False, True], ids=["forward", "reversed"])
@pytest.mark.parametrize(
    ("compatibility", "resolves"),
    [
        pytest.param((), False, id="direct-requirement-is-not-a-forced-override"),
        pytest.param((("1.1", "1.0"),), False, id="partial-chain-is-insufficient"),
        pytest.param((("1.2", "1.1"), ("1.1", "1.0")), True, id="complete-transitive-chain"),
    ],
)
def test_direct_requirement_needs_compatibility_with_both_branches(
    catalog: Catalog,
    reverse: bool,
    compatibility: tuple[tuple[str, str], ...],
    resolves: bool,
) -> None:
    game, records, relations = _diamond("1.0", "1.1", compatibility, direct="1.2")
    catalog.publish(
        records[::-1] if reverse else records,
        relations=relations[::-1] if reverse else relations,
    )
    if resolves:
        result = catalog.graph.dependency_closure(game.ref, resolve_versions=True)
        incoming = tuple(edge for edge in result.relations if edge.target.name == "matrix")
        assert {edge.source for edge in incoming} == {
            game.ref,
            release("engine").ref,
            release("intro").ref,
        }
        assert {edge.target for edge in incoming} == {release("matrix", "1.2").ref}
        assert len(result.records) == 4
    else:
        with pytest.raises(VersionConflict, match="Incompatible versions for matrix"):
            catalog.graph.dependency_closure(game.ref, resolve_versions=True)
    for name, version in (("engine", "1.0"), ("intro", "1.1")):
        assert (
            catalog.graph.dependencies(release(name).ref)[0].record.ref
            == release("matrix", version).ref
        )
