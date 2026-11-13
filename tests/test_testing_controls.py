"""Server outages and operation budgets exercise real Conan and Git boundaries."""

from pathlib import Path
from unittest.mock import patch

import pytest

from coreref.api.errors import CatalogError
from coreref.api.graph import Graph
from coreref.api.query import SearchQuery
from coreref.testing import LocalServer, Sandbox, local_server, trace_operations

pytestmark = pytest.mark.conan_remote


def test_stop_start_restart_keep_registry_identity_and_recover(tmp_path: Path) -> None:
    with local_server(tmp_path / "server") as server:
        env = Sandbox(server, tmp_path / "fixtures")
        catalog = env.new_catalog(timeout=0.2)
        published = catalog.publish(env.package(env.ref("cpu"), files={"result": "result"}))
        identity = (server.url, server.password)
        server.stop()
        server.stop()
        assert not server.running
        with pytest.raises(CatalogError):
            catalog.search_refs(SearchQuery())
        server.start()
        server.start()
        assert server.running and (server.url, server.password) == identity
        assert catalog.search_refs(SearchQuery()) == (published.ref,)
        server.restart()
        assert (server.url, server.password) == identity
        assert env.new_catalog().get(published.ref).files["result"].read_text() == "result"
    assert not server.running


def test_operation_trace_counts_real_fetch_then_cached_reuse(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    env = Sandbox(conan_server, tmp_path)
    publisher = env.catalog
    published = publisher.publish(
        env.package(
            env.ref("cpu"),
            sources={"cpu.sv": "RTL"},
            files={"result": "result"},
        )
    )
    reader = env.new_catalog()
    with trace_operations() as resolution:
        graph = Graph(reader, published.ref)
    assert resolution.recipe_downloads == 1
    assert resolution.recipe_reads == 1
    assert resolution.installs == resolution.source_clones == resolution.package_downloads == 0
    with trace_operations() as fetch:
        assert len(graph.get_with_sources()) == 1
    assert fetch.installs == fetch.package_downloads == fetch.source_clones == 1
    assert fetch.recipe_reads == fetch.recipe_downloads == 0
    with trace_operations() as cached:
        assert len(graph.get_with_sources()) == 1
    assert cached.commands == ()
    assert cached.installs == cached.source_clones == cached.package_downloads == 0


def test_trace_restores_instrumentation_after_exception_and_retains_counts(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    env = Sandbox(conan_server, tmp_path)
    catalog = env.catalog
    with pytest.raises(RuntimeError, match="caller failed"), trace_operations() as trace:
        assert catalog.search_refs(SearchQuery()) == ()
        raise RuntimeError("caller failed")
    before = trace.commands
    assert before
    catalog.search_refs(SearchQuery())
    assert trace.commands == before


def test_diagnostics_have_paths_and_published_refs_without_io_or_password(
    conan_server: LocalServer,
    tmp_path: Path,
) -> None:
    env = Sandbox(conan_server, tmp_path)
    publisher = env.catalog
    record = publisher.publish(env.package(env.ref("cpu")))
    with patch("requests.sessions.Session.send", side_effect=AssertionError("Unexpected I/O")):
        text = env.diagnostics()
    assert str(record.ref) in text
    assert str(env.projects) in text
    assert str(conan_server.log_path) in text
    assert "Conan home:" in text and str(tmp_path / "client-0") in text
    assert conan_server.password not in text


def test_context_reaps_restarted_server_after_caller_failure(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="caller failed"), local_server(tmp_path) as server:
        server.restart()
        assert server.running
        raise RuntimeError("caller failed")
    assert not server.running
