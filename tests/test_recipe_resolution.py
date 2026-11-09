"""Recipe resolution reuses Conan's cache and filters before reading metadata."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from coreref._internal.recipe import LocalRecipe
from coreref.api.catalog import Catalog
from coreref.api.errors import CatalogError
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import InstalledRecord, LocalRecord, Ref
from examples.local_server import LocalServer


@pytest.fixture
def tree(catalog: Catalog) -> tuple[InstalledRecord, InstalledRecord, InstalledRecord]:
    left = catalog.publish(LocalRecord(Ref("soc.left.rtl.default/1.0", catalog=catalog)))
    right = catalog.publish(LocalRecord(Ref("soc.right.rtl.default/1.0", catalog=catalog)))
    root = catalog.publish(
        LocalRecord(Ref("soc.root.rtl.default/1.0", catalog=catalog)),
        dependencies=(left.ref, right.ref),
    )
    return left, right, root


def test_cached_exact_tree_needs_no_network(
    catalog: Catalog, tree: tuple[InstalledRecord, InstalledRecord, InstalledRecord]
) -> None:
    left, right, root = tree
    with patch("requests.sessions.Session.send", side_effect=AssertionError("Unexpected HTTP")):
        assert root.ref.info() == root
        assert root.ref.get() == root
        assert root.ref.graph().dependencies() == (left, right)
        graph = root.ref.graph()
        assert graph.records == tuple(sorted(tree, key=lambda record: record.ref))
        assert graph.get((root.ref, left.ref)) == (root, left)
        local = LocalRecord(Ref("soc.consumer.rtl.default/1.0", catalog=catalog))
        assert Graph(local, dependencies=(root.ref,)).requirements == (root.ref,)


@pytest.mark.parametrize("published", [False, True])
def test_fresh_graph_reads_recipes_from_native_paths(
    conan_server: LocalServer,
    tmp_path: Path,
    tree: tuple[InstalledRecord, InstalledRecord, InstalledRecord],
    *,
    published: bool,
) -> None:
    reader = Catalog(conan_server.configure(tmp_path / "reader"))
    ref = Ref(str(tree[-1].ref), catalog=reader)
    local = LocalRecord(Ref("soc.consumer.rtl.default/1.0", catalog=reader))
    with (
        patch.object(reader._client, "run", wraps=reader._client.run) as commands,
        patch.object(reader._client, "download_recipe", side_effect=AssertionError("Reload")),
    ):
        graph = ref.graph() if published else Graph(local, dependencies=(ref,))
        assert all(record in graph.records for record in tree)
        assert commands.call_count == 1
        assert commands.call_args.args[:2] == ("graph", "info")
        assert "--update" not in commands.call_args.args
        assert graph.get(tuple(record.ref for record in tree)) == tree
        assert commands.call_count == 1


def test_unpinned_graph_resolves_before_metadata_reads(
    conan_server: LocalServer,
    tmp_path: Path,
    tree: tuple[InstalledRecord, InstalledRecord, InstalledRecord],
) -> None:
    reader = Catalog(conan_server.configure(tmp_path / "reader"))
    ref = Ref(str(tree[-1].ref.without_revision()), catalog=reader)
    local = LocalRecord(Ref("soc.consumer.rtl.default/1.0", catalog=reader))
    with patch.object(reader._client, "run", wraps=reader._client.run) as commands:
        graph = Graph(local, dependencies=(ref,))
        assert commands.call_count == 1
        assert commands.call_args.args[:2] == ("graph", "info")
        assert "--update" in commands.call_args.args
    assert graph.requirements == (tree[-1].ref,)
    lock = graph.create_lockfile(tmp_path / "project.lock")
    with patch.object(reader._client, "run", wraps=reader._client.run) as commands:
        assert Graph(local, dependencies=(ref,), lockfile=lock).records == graph.records
        assert commands.call_count == 1
        assert "--update" not in commands.call_args.args


def test_revision_filter_on_floating_requirements_uses_latest(
    catalog: Catalog, tmp_path: Path
) -> None:
    ref = Ref("soc.leaf.rtl.default/1.0", catalog=catalog)
    first = catalog.publish(LocalRecord(ref, {"generation": 1}))
    root = LocalRecord(Ref("soc.external.rtl.default/1.0", catalog=catalog))
    LocalRecipe(root, (str(ref),)).write(tmp_path / "recipe")
    catalog._client.upload(catalog._client.export(tmp_path / "recipe"))
    second = catalog.publish(LocalRecord(ref, {"generation": 2}))
    assert root.ref.graph().dependencies(filters=SearchQuery(revision=second.ref.revision)) == (
        second,
    )
    assert root.ref.graph().dependencies(filters=SearchQuery(revision=first.ref.revision)) == ()
    assert (
        root.ref.graph().dependencies(
            filters=SearchQuery(component="other", revision=first.ref.revision)
        )
        == ()
    )


def test_cache_errors_do_not_trigger_remote_fallback(catalog: Catalog) -> None:
    with (
        patch.object(catalog._client, "cache_path", side_effect=CatalogError("Unreadable cache")),
        patch.object(catalog._client, "run", side_effect=AssertionError("Unexpected download")),
        pytest.raises(CatalogError, match="Unreadable cache"),
    ):
        catalog._client.download_recipe("soc.root.rtl.default/1.0#" + "a" * 32)


def test_pinned_external_recipe_refreshes_floating_dependencies(
    catalog: Catalog, conan_server: LocalServer, tmp_path: Path
) -> None:
    logical = Ref("soc.leaf.rtl.default/1.0", catalog=catalog)
    first = catalog.publish(LocalRecord(logical, {"generation": 1}))
    external = LocalRecord(Ref("soc.external.rtl.default/1.0", catalog=catalog))
    LocalRecipe(external, (str(logical),)).write(tmp_path / "recipe")
    pinned = catalog._client.export(tmp_path / "recipe")
    catalog._client.upload(pinned)
    reader = Catalog(conan_server.configure(tmp_path / "reader"))
    ref = Ref(pinned, catalog=reader)
    lock = ref.graph().create_lockfile(tmp_path / "external.lock")
    second = catalog.publish(LocalRecord(logical, {"generation": 2}))
    with patch.object(reader._client, "run", wraps=reader._client.run) as commands:
        assert ref.graph().requirements == (second.ref,)
        calls = [call.args for call in commands.call_args_list]
        assert len(calls) == 2
        assert all(args[:2] == ("graph", "info") for args in calls)
        assert "--update" not in calls[0] and "--update" in calls[1]
    assert ref.graph(lockfile=lock).requirements == (first.ref,)
    local = LocalRecord(Ref("soc.consumer.rtl.default/1.0", catalog=reader))
    assert second in Graph(local, dependencies=(ref,)).records
