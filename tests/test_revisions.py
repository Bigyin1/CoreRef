"""Recipe revisions survive public operations and project snapshots."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coreref._internal.recipe import LocalRecipe
from coreref._internal.references import from_conan_reference, to_conan_reference
from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError, InvalidPackage, NotFound
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Record, Ref


def test_revision_identity_and_snapshots(catalog: Catalog) -> None:
    logical = Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)
    first = Ref.from_fields(**logical.to_dict(), revision="a" * 32, catalog=catalog)
    second = Ref.from_fields(**logical.to_dict(), revision="b" * 32, catalog=catalog)
    assert len({logical, first, second}) == 3
    assert sorted((second, logical, first)) == [logical, first, second]
    assert first.without_revision() == logical
    assert first.without_revision()._catalog is catalog
    assert str(first) == f"{logical}#{'a' * 32}"
    assert from_conan_reference(str(first) + "%123.0", catalog=catalog) == first
    assert to_conan_reference(first) == str(first)
    record = Record(first, {"generation": 1})
    assert json.loads(json.dumps(record.to_dict()))["ref"] == first.to_dict()
    assert "revision" not in json.loads(LocalRecord(first).to_json())["ref"]


@pytest.mark.parametrize("revision", ["", "latest", "*", "a#b", "a/b", "a%123", "a\nb"])
def test_revision_must_be_a_concrete_token(catalog: Catalog, revision: str) -> None:
    with pytest.raises(InvalidPackage):
        ref = Ref.from_fields(
            "soc", "cpu", "rtl", "default", "1.0", revision=revision, catalog=catalog
        )
        to_conan_reference(ref)


def test_search_selects_latest_history_and_explicit_revision(catalog: Catalog) -> None:
    ref = Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)
    first = catalog.publish(LocalRecord(ref, {"generation": 1}))
    second = catalog.publish(LocalRecord(ref, {"generation": 2}))
    assert first.ref.revision and second.ref.revision
    assert first.ref != second.ref
    assert first.ref.get() == first
    assert ref.get() == second
    assert catalog.search(SearchQuery()) == (second,)
    assert set(catalog.search(SearchQuery(), all_revisions=True)) == {first, second}
    query = SearchQuery(revision=first.ref.revision)
    assert catalog.search(query) == (first,)
    assert catalog.search(query, all_revisions=True) == (first,)
    assert catalog.publish(LocalRecord(ref, {"generation": 2})).ref == second.ref
    assert len(catalog.search(SearchQuery(), all_revisions=True)) == 2


def test_query_builds_revision_patterns_directly() -> None:
    query = SearchQuery(component="cpu")
    assert query._conan_pattern() == "*.cpu*.*.*/*#latest"
    assert query._conan_pattern(all_revisions=True) == "*.cpu*.*.*/*#*"
    exact = SearchQuery(revision="a" * 32)
    assert exact._conan_pattern() == exact._conan_pattern(all_revisions=True)
    assert exact._conan_pattern() == f"*.*.*.*/*#{'a' * 32}"
    assert SearchQuery(revision="*")._conan_pattern() is None


@pytest.mark.parametrize("operation", ["publish", "graph"])
def test_local_roots_cannot_claim_a_revision(catalog: Catalog, operation: str) -> None:
    local = LocalRecord(
        Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", revision="a" * 32, catalog=catalog)
    )
    with pytest.raises(InvalidPackage, match="local package root"):
        if operation == "publish":
            catalog.publish(local)
        else:
            Graph(local)
    assert catalog.search(SearchQuery()) == ()


def test_lockfile_and_graph_keep_old_files_after_new_revision(
    catalog: Catalog, tmp_path: Path
) -> None:
    ref = Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)
    file = tmp_path / "report"
    file.write_text("first")
    first = catalog.publish(LocalRecord(ref, files={"report": file}))
    root = LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=catalog))
    graph = Graph(root, dependencies=(ref,))
    lock = graph.create_lockfile(tmp_path / "project.lock")
    contents = lock.read_bytes()
    file.write_text("second")
    second = catalog.publish(LocalRecord(ref, files={"report": file}))
    assert graph.ordered_refs(root.ref) == (first.ref, root.ref)
    assert graph.get((ref,))[0].files["report"].read_text() == "first"
    assert Graph(root, dependencies=(ref,)).requirements == (second.ref,)
    locked = Graph(root, dependencies=(ref,), lockfile=lock)
    assert locked.requirements == (first.ref,)
    published = catalog.publish(root, dependencies=(ref,), lockfile=lock)
    assert published.ref.graph().dependencies() == (first,)
    assert lock.read_bytes() == contents
    with pytest.raises(CatalogError, match="lockfile"):
        Graph(root, dependencies=(second.ref,), lockfile=lock)
    with pytest.raises(InvalidPackage, match="Revision"):
        graph.get((second.ref,))
    with pytest.raises(ValueError, match="Revision"):
        graph.ordered_refs(second.ref)


def test_missing_explicit_revision_does_not_fall_back(catalog: Catalog) -> None:
    ref = Ref.from_fields("soc", "cpu", "rtl", "default", "1.0", catalog=catalog)
    catalog.publish(LocalRecord(ref))
    missing = Ref.from_fields(**ref.to_dict(), revision="a" * 32, catalog=catalog)
    with pytest.raises(NotFound):
        missing.get()


def test_external_unpinned_requirements_resolve_independently(
    catalog: Catalog, tmp_path: Path
) -> None:
    leaves = tuple(
        catalog.publish(
            LocalRecord(Ref.from_fields("soc", name, "rtl", "default", "1.0", catalog=catalog))
        )
        for name in ("left", "right")
    )
    root = LocalRecord(Ref.from_fields("soc", "external", "rtl", "default", "1.0", catalog=catalog))
    LocalRecipe(root, tuple(str(record.ref.without_revision()) for record in leaves)).write(
        tmp_path
    )
    catalog._client.upload(catalog._client.export(tmp_path))
    assert root.ref.graph().dependencies() == leaves
