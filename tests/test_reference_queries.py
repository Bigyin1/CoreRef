"""Coordinate-only queries keep ordinary callers independent of revision selection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import InvalidPackage
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Ref
from coreref.testing import LocalServer


def test_search_refs_reads_only_the_index_and_defaults_to_one_revision(
    catalog: Catalog, conan_server: LocalServer, tmp_path: Path
) -> None:
    logical = Ref("soc.cpu.rtl.default/1.0")
    first = catalog.publish(LocalRecord(logical, {"generation": 1}))
    second = catalog.publish(LocalRecord(logical, {"generation": 2}))
    part = catalog.publish(LocalRecord(Ref("soc.cpu.alu.be.lib-lef/1.0")))
    reader = Catalog(conan_server.configure(tmp_path / "reader"))
    with (
        patch.object(reader._client, "download_recipe", side_effect=AssertionError("Metadata")),
        patch.object(reader._client, "run", wraps=reader._client.run) as commands,
    ):
        refs = reader.search_refs(SearchQuery())
        assert refs == (second.ref, part.ref)
        assert [call.args[0] for call in commands.call_args_list] == ["list"]
        assert reader.search_refs(SearchQuery(), all_revisions=True) == tuple(
            sorted((first.ref, second.ref, part.ref))
        )
        assert reader.search_refs(SearchQuery(subcomponent=None)) == (second.ref,)
        assert reader.search_refs(SearchQuery(revision=first.ref.revision)) == (first.ref,)
        assert reader.search_refs(SearchQuery(component="absent")) == ()
        commands.reset_mock()
        with pytest.raises(InvalidPackage):
            SearchQuery(component="cpu*")
        commands.assert_not_called()
    assert reader.search_refs(SearchQuery()) == tuple(
        record.ref for record in reader.search(SearchQuery())
    )
