"""Published roots expose the same fixed graph and batch retrieval as local projects."""

from pathlib import Path
from unittest.mock import patch

import networkx as nx
import pytest

from examples.local_server import LocalServer
from hwcatalog._internal import recipe as recipe_module
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import InvalidPackage, NotFound
from hwcatalog.api.graph import Dependency, Graph
from hwcatalog.api.records import LocalRecord, Record, Ref


def test_published_graph_has_an_exact_root_without_a_consumer_node(catalog: Catalog) -> None:
    leaf = catalog.publish(
        LocalRecord(Ref.from_fields("soc", "leaf", "rtl", "default", "1.0", catalog=catalog))
    )
    root = catalog.publish(
        LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=catalog)),
        dependencies=(leaf.ref,),
    )
    graph = root.ref.without_revision().graph()
    assert graph.record == root
    assert type(graph.record) is Record
    assert graph.requirements == (leaf.ref,)
    assert graph.edges == (Dependency(root.ref, leaf.ref),)
    assert graph.ordered_refs(root.ref.without_revision()) == (leaf.ref, root.ref)
    assert graph.ordered_records(root.ref) == (leaf, root)
    assert graph.networkx is graph.networkx
    with pytest.raises(nx.NetworkXError):
        graph.networkx.remove_node(root.ref)
    with patch.object(catalog._client, "run", side_effect=AssertionError("Unexpected Conan I/O")):
        assert graph.get((root.ref.without_revision(), leaf.ref)) == (root, leaf)
        assert graph.get_with_sources((root.ref,))[0] is graph.get((root.ref,))[0]
    with pytest.raises(InvalidPackage, match="requirements come from"):
        Graph(catalog, root.ref, dependencies=(leaf.ref,))
    with pytest.raises(NotFound):
        Ref.from_fields("soc", "missing", "rtl", "default", "1.0", catalog=catalog).graph()


def test_published_tree_installs_root_and_dependencies_in_one_batch(
    catalog: Catalog, checkout: Path, tmp_path: Path, conan_server: LocalServer
) -> None:
    artifact = tmp_path / "report"
    artifact.write_text("data")
    leaf = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "leaf", "rtl", "default", "1.0", catalog=catalog),
            source=checkout,
            files={"report": artifact},
        )
    )
    root = catalog.publish(
        LocalRecord(
            Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=catalog),
            source=checkout,
            files={"report": artifact},
        ),
        dependencies=(leaf.ref,),
    )
    reader = Catalog(conan_server.configure(tmp_path / "reader", local_sources=True))
    requested = Ref.from_fields(**root.ref.to_dict(), catalog=reader)
    with (
        patch.object(reader._client, "download_recipe", wraps=reader._client.download_recipe) as dl,
        patch.object(reader._client, "run", wraps=reader._client.run) as run,
    ):
        graph = requested.graph()
        assert dl.call_count == 2
        assert sum(call.args[:2] == ("graph", "info") for call in run.call_args_list) == 1
        assert not any(call.args[0] == "install" for call in run.call_args_list)
        records = graph.get_with_sources(graph.ordered_refs(requested))
        assert tuple(record.ref for record in records) == (leaf.ref, root.ref)
        assert all(record.files["report"].read_text() == "data" for record in records)
        assert all(record.source and (record.source / "cpu.sv").is_file() for record in records)
        assert graph.get((requested,))[0] is records[1]
        assert dl.call_count == 2
        assert sum(call.args[0] == "install" for call in run.call_args_list) == 1


def test_published_graph_and_lock_keep_the_root_revision(catalog: Catalog, tmp_path: Path) -> None:
    artifact = tmp_path / "report"
    artifact.write_text("first")
    local = LocalRecord(
        Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=catalog),
        files={"report": artifact},
    )
    first = catalog.publish(local)
    graph = first.ref.without_revision().graph()
    lock = graph.create_lockfile(tmp_path / "stored.lock")
    artifact.write_text("second")
    second = catalog.publish(local)
    assert second.ref != first.ref
    assert first.ref.without_revision().graph().record.ref == second.ref
    assert first.ref.without_revision().graph(lockfile=lock).record.ref == first.ref
    assert graph.get((first.ref.without_revision(),))[0].files["report"].read_text() == "first"
    assert first.ref.graph().get((first.ref,))[0].files["report"].read_text() == "first"
    with pytest.raises(InvalidPackage, match="Revision is not part"):
        graph.get((second.ref,))
    with pytest.raises(ValueError, match="Revision is not part"):
        graph.ordered_refs(second.ref)
    with pytest.raises(InvalidPackage):
        second.ref.graph(lockfile=lock)


def test_template_changes_preserve_published_recipe_readability(
    catalog: Catalog, monkeypatch: pytest.MonkeyPatch
) -> None:
    local = LocalRecord(Ref.from_fields("soc", "top", "rtl", "default", "1.0", catalog=catalog))
    first = catalog.publish(local)
    render = recipe_module._recipe_text

    def changed_template(ref: Ref) -> str:
        return render(ref) + "\n# Template documentation changed.\n"

    monkeypatch.setattr(recipe_module, "_recipe_text", changed_template)
    second = catalog.publish(local)
    assert first.ref != second.ref
    assert first.ref.info() == first
    assert first.ref.graph().records == (first,)
    assert first.ref.get() == first
    assert second.ref.get() == second
