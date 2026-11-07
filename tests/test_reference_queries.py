"""Coordinate-only queries keep ordinary callers independent of revision selection."""

from pathlib import Path
from unittest.mock import patch

import pytest

from examples.local_server import LocalServer
from hwcatalog._internal.recipe import LocalRecipe
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import NotFound
from hwcatalog.api.query import SearchQuery
from hwcatalog.api.records import LocalRecord, Ref


def test_search_refs_reads_only_the_index_and_defaults_to_one_revision(
    catalog: Catalog, conan_server: LocalServer, tmp_path: Path
) -> None:
    logical = Ref("soc.cpu.rtl.default/1.0", catalog=catalog)
    first = catalog.publish(LocalRecord(logical, {"generation": 1}))
    second = catalog.publish(LocalRecord(logical, {"generation": 2}))
    part = catalog.publish(LocalRecord(Ref("soc.cpu_alu.be.lib-lef/1.0", catalog=catalog)))
    reader = Catalog(conan_server.configure(tmp_path / "reader"))
    with (
        patch.object(reader._client, "download_recipe", side_effect=AssertionError("Metadata")),
        patch.object(reader._client, "run", wraps=reader._client.run) as commands,
    ):
        refs = reader.search_refs()
        assert refs == (second.ref, part.ref)
        assert all(ref._catalog is reader for ref in refs)
        assert [call.args[0] for call in commands.call_args_list] == ["list"]
        assert reader.search_refs(all_revisions=True) == tuple(
            sorted((first.ref, second.ref, part.ref))
        )
        assert reader.search_refs(SearchQuery(subcomponent=None)) == (second.ref,)
        assert reader.search_refs(SearchQuery(revision=first.ref.revision)) == (first.ref,)
        assert reader.search_refs(SearchQuery(component="absent")) == ()
        commands.reset_mock()
        assert reader.search_refs(SearchQuery(component="cpu*")) == ()
        commands.assert_not_called()
    assert reader.search_refs() == tuple(record.ref for record in reader.search())


def test_dependency_refs_downloads_only_the_parent_recipe(
    catalog: Catalog, conan_server: LocalServer, tmp_path: Path
) -> None:
    leaf = catalog.publish(LocalRecord(Ref("soc.leaf.rtl.default/1.0", catalog=catalog)))
    parent = catalog.publish(
        LocalRecord(Ref("soc.parent.rtl.default/1.0", catalog=catalog)),
        dependencies=(leaf.ref.without_revision(),),
    )
    reader = Catalog(conan_server.configure(tmp_path / "reader"))
    requested = Ref("soc.parent.rtl.default/1.0", catalog=reader)
    with patch.object(
        reader._client, "download_recipe", wraps=reader._client.download_recipe
    ) as read:
        refs = requested.dependency_refs()
        assert refs == (leaf.ref,)
        assert refs[0]._catalog is reader
        assert [call.args[0] for call in read.call_args_list] == [str(parent.ref)]
    with pytest.raises(NotFound):
        reader._client.cache_path(str(leaf.ref))
    assert requested.dependency_refs(SearchQuery(view="be")) == ()
    assert requested.dependency_refs(SearchQuery(revision=leaf.ref.revision)) == refs
    assert requested.dependencies()[0].ref == refs[0]


def test_floating_dependency_refs_select_latest_without_reading_child_metadata(
    catalog: Catalog, tmp_path: Path
) -> None:
    logical = Ref("soc.leaf.rtl.default/1.0", catalog=catalog)
    first = catalog.publish(LocalRecord(logical))
    parent = LocalRecord(Ref("soc.external.rtl.default/1.0", catalog=catalog))
    LocalRecipe(parent, (str(logical),)).write(tmp_path / "recipe")
    reference = catalog._client.export(tmp_path / "recipe")
    catalog._client.upload(reference)
    second = catalog.publish(LocalRecord(logical, {"generation": 2}))
    folder = catalog._client.cache_path(str(second.ref))
    (folder / "record.json").unlink()
    with patch.object(
        catalog._client, "download_recipe", wraps=catalog._client.download_recipe
    ) as read:
        assert parent.ref.dependency_refs() == (second.ref,)
        assert parent.ref.dependency_refs(SearchQuery(revision=first.ref.revision)) == ()
        assert parent.ref.dependency_refs(SearchQuery(revision=second.ref.revision)) == (
            second.ref,
        )
        assert all(call.args[0] == reference for call in read.call_args_list)
