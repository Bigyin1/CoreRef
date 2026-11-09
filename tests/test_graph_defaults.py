"""Whole-tree operations use a fixed graph, with dependency-first default retrieval."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import InvalidPackage
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord, Ref


def test_transitive_filters_do_not_prune_intermediate_nodes(catalog: Catalog) -> None:
    leaf = catalog.publish(LocalRecord(Ref("soc.z-leaf.rtl.default/1.0", catalog=catalog)))
    middle = catalog.publish(
        LocalRecord(Ref("soc.a-middle.be.netlist/1.0", catalog=catalog)),
        dependencies=(leaf.ref,),
    )
    local = LocalRecord(Ref("soc.top.rtl.default/1.0", catalog=catalog))
    graph = Graph(local, dependencies=(middle.ref, leaf.ref))
    newer = catalog.publish(LocalRecord(leaf.ref.without_revision(), {"generation": 2}))
    with patch.object(catalog._client, "run", side_effect=AssertionError("Unexpected I/O")):
        assert graph.dependencies() == (middle, leaf)
        assert graph.dependencies(transitive=True) == (leaf, middle)
        assert graph.dependencies(transitive=True, filters=SearchQuery(view="rtl")) == (leaf,)
        assert graph.dependencies(middle.ref.without_revision(), transitive=True) == (leaf,)
        assert graph.dependencies(leaf.ref.without_revision(), transitive=True) == ()
        assert graph.dependencies(transitive=True, filters=SearchQuery(component="absent")) == ()
        with pytest.raises(InvalidPackage, match="Revision"):
            graph.dependencies(newer.ref, transitive=True)
        with pytest.raises(InvalidPackage, match="Unknown"):
            graph.dependencies(Ref("soc.absent.rtl.default/1.0", catalog=catalog), transitive=True)


@pytest.mark.parametrize("published", [False, True])
def test_default_retrieval_batches_the_tree_and_preserves_snapshot(
    catalog: Catalog, checkout: Path, tmp_path: Path, *, published: bool
) -> None:
    artifact = tmp_path / "report"
    artifact.write_text("original")
    leaf = catalog.publish(
        LocalRecord(Ref("soc.z-leaf.rtl.default/1.0", catalog=catalog), source=checkout)
    )
    root = LocalRecord(
        Ref("soc.a-root.rtl.default/1.0", catalog=catalog),
        source=checkout,
        files={"report": artifact},
    )
    stored = catalog.publish(root, dependencies=(leaf.ref.without_revision(),))
    graph = (
        root.ref.graph() if published else Graph(root, dependencies=(leaf.ref.without_revision(),))
    )
    artifact.write_text("later")
    catalog.publish(root, dependencies=(leaf.ref,))
    with patch.object(catalog._client, "run", wraps=catalog._client.run) as commands:
        assert graph.get(()) == graph.get_with_sources(()) == ()
        commands.assert_not_called()
        ready = graph.get_with_sources()
        expected = (leaf.ref, stored.ref) if published else (leaf.ref,)
        assert tuple(record.ref for record in ready) == expected
        assert all(record.source and (record.source / "cpu.sv").is_file() for record in ready)
        if published:
            assert ready[-1].files["report"].read_text() == "original"
        assert graph.get() == ready
        assert graph.get_with_sources() == ready
        assert graph.get((leaf.ref.without_revision(),))[0] is ready[0]
        assert [call.args[0] for call in commands.call_args_list] == ["install"]


def test_default_get_leaves_sources_for_an_explicit_source_request(
    catalog: Catalog, checkout: Path, tmp_path: Path
) -> None:
    artifact = tmp_path / "report"
    artifact.write_text("data")
    local = LocalRecord(
        Ref("soc.a-root.rtl.default/1.0", catalog=catalog),
        source=checkout,
        files={"report": artifact},
    )
    catalog.publish(local)
    graph = local.ref.graph()
    with patch.object(catalog._client, "run", wraps=catalog._client.run) as commands:
        ready = graph.get()
        assert ready[0].source is None and ready[0].files["report"].read_text() == "data"
        assert graph.get()[0] is ready[0]
        assert len(commands.call_args_list) == 1
        sources = graph.get_with_sources()
        assert sources[0].source is not None
        assert graph.get()[0] is sources[0]
        assert [call.args[0] for call in commands.call_args_list] == ["install", "install"]


def test_empty_local_graph_defaults_do_not_run_conan(catalog: Catalog) -> None:
    graph = Graph(LocalRecord(Ref("soc.empty.rtl.default/1.0", catalog=catalog)))
    with patch.object(catalog._client, "run", side_effect=AssertionError("Unexpected I/O")):
        assert graph.dependencies(transitive=True) == ()
        assert graph.get() == graph.get_with_sources() == ()
