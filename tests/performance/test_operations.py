"""Deterministic I/O budgets run separately from uninstrumented timing samples."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conan.tools.scm import Git  # type: ignore[import-untyped]
from requests.sessions import Session

from coreref._internal.recipe import StoredRecipe
from coreref.api.query import SearchQuery
from coreref.api.records import LocalRecord
from tests.performance.support import Registry, Shape, check_files

pytestmark = [pytest.mark.performance, pytest.mark.conan_remote]


@contextmanager
def recipe_reads() -> Iterator[MagicMock]:
    with patch.object(
        StoredRecipe, "__init__", autospec=True, side_effect=StoredRecipe.__init__
    ) as reads:
        yield reads
        references = [call.kwargs["reference"] for call in reads.call_args_list]
        assert len(references) == len(set(references)), (
            "Recipe read more than once in one operation"
        )


@pytest.mark.parametrize("shape", ["chain", "wide", "shared"])
@pytest.mark.parametrize("size", [8, 32])
def test_resolution_budget(registry: Registry, shape: Shape, size: int) -> None:
    dataset = registry.dataset(shape, size)
    reader = registry.reader()
    with (
        patch.object(reader._client, "run", wraps=reader._client.run) as commands,
        recipe_reads() as reads,
    ):
        dataset.check(dataset.resolve(reader))
    assert [call.args[:2] for call in commands.call_args_list] == [("graph", "info")]
    assert reads.call_count == size


def test_lock_keeps_revision_without_preselection(registry: Registry, tmp_path: Path) -> None:
    dataset = registry.dataset("chain", 2)
    reader = registry.reader()
    lock = dataset.resolve(reader).create_lockfile(tmp_path / "project.lock")
    original = dataset.records[-1].ref
    newer = registry.publisher.publish(
        LocalRecord(original.without_revision(), payload={"new": True}),
        dependencies=(dataset.records[0].ref,),
    )
    assert newer.ref != original
    reader = registry.reader()
    with patch.object(reader._client, "run", wraps=reader._client.run) as commands:
        graph = dataset.resolve(reader, lockfile=lock)
    dataset.check(graph)
    assert len(commands.call_args_list) == 1
    assert "--update" not in commands.call_args.args


def test_incremental_install_budget(registry: Registry) -> None:
    graph = registry.dataset("shared", 8, "mixed").resolve(registry.reader())
    selected = graph.ordered_refs(graph.requirements[-1])
    remaining = tuple(
        ref for ref in graph.ordered_refs(graph.record.ref)[:-1] if ref not in selected
    )
    with (
        patch.object(graph.catalog._client, "run", wraps=graph.catalog._client.run) as commands,
        patch.object(Session, "send", autospec=True, side_effect=Session.send) as requests,
        patch.object(Git, "clone", autospec=True, side_effect=Git.clone) as clones,
        recipe_reads() as reads,
    ):
        first = graph.get_with_sources(selected)
        assert clones.call_count == sum(record.has_source for record in first)
        rest = graph.get_with_sources(remaining)
        assert clones.call_count == sum(record.has_source for record in first + rest)
        assert [call.args[0] for call in commands.call_args_list] == ["install", "install"]
        archives = [
            call.args[1].url
            for call in requests.call_args_list
            if call.args[1].method == "GET" and "conan_package.tgz" in call.args[1].url
        ]
        assert len(archives) == len(set(archives)), "Repeated binary download"
        commands.reset_mock()
        requests.reset_mock()
        by_ref = {record.ref: record for record in first + rest}
        assert graph.get_with_sources() == tuple(
            by_ref[ref] for ref in graph.ordered_refs(graph.record.ref)[:-1]
        )
        assert not commands.called and not requests.called
        assert reads.call_count == 0
    check_files(first + rest, sources=True)


@pytest.mark.parametrize("sources", [False, True], ids=["files-only", "with-sources"])
def test_full_install_budget(registry: Registry, *, sources: bool) -> None:
    graph = registry.dataset("shared", 8, "mixed").resolve(registry.reader())
    with (
        patch.object(graph.catalog._client, "run", wraps=graph.catalog._client.run) as commands,
        patch.object(Session, "send", autospec=True, side_effect=Session.send) as requests,
        recipe_reads() as reads,
    ):
        records = graph.get_with_sources() if sources else graph.get()
    assert [call.args[0] for call in commands.call_args_list] == ["install"]
    assert reads.call_count == 0
    archives = Counter(
        call.args[1].url
        for call in requests.call_args_list
        if call.args[1].method == "GET" and "conan_package.tgz" in call.args[1].url
    )
    assert archives and max(archives.values()) == 1
    check_files(records, sources=sources)


def test_search_refs_reads_no_recipes(registry: Registry) -> None:
    dataset = registry.dataset("wide", 100)
    reader = registry.reader()
    query = SearchQuery(namespace=dataset.namespace, view="rtl")
    with (
        patch.object(reader._client, "run", wraps=reader._client.run) as commands,
        recipe_reads() as reads,
    ):
        assert set(reader.search_refs(query)) == {r.ref for r in dataset.records}
    assert reads.call_count == 0
    assert [call.args[0] for call in commands.call_args_list] == ["list"]
    with recipe_reads() as reads:
        assert {r.ref for r in reader.search(query)} == {r.ref for r in dataset.records}
    assert reads.call_count == len(dataset.records)


def test_snapshot_queries_do_not_perform_io(registry: Registry) -> None:
    graph = registry.dataset("shared", 32).resolve(registry.reader())
    with (
        patch.object(graph.catalog._client, "run", side_effect=AssertionError("Conan I/O")),
        patch.object(Session, "send", side_effect=AssertionError("Network I/O")),
        patch.object(StoredRecipe, "__init__", side_effect=AssertionError("Recipe I/O")),
    ):
        assert graph.networkx is graph.networkx
        order = graph.ordered_refs(graph.record.ref)
        assert graph.ordered_refs(graph.record.ref) is order
        stops = (graph.requirements[-1],)
        pruned = graph.ordered_refs(graph.record.ref, stop_at=stops)
        assert graph.ordered_refs(graph.record.ref, stop_at=stops) is pruned
        assert len(graph.dependencies(transitive=True)) == 32
        assert all(graph.info(ref).ref == ref for ref in order)
