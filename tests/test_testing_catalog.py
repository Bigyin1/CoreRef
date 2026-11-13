"""A sandbox shares its default client and creates cold clients only on request."""

from pathlib import Path
from unittest.mock import patch

import pytest

from coreref.api.graph import Graph
from coreref.testing import LocalServer, Sandbox

pytestmark = pytest.mark.conan_remote


def test_default_catalog_is_lazy_and_belongs_to_one_environment(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    first = Sandbox(conan_server, tmp_path / "first")
    second = Sandbox(conan_server, tmp_path / "second")
    with patch.object(conan_server, "configure", wraps=conan_server.configure) as configure:
        first.project(first.ref("consumer"))
        first.package(first.ref("cpu"), files={"report": "result"})
        first.diagnostics()
        configure.assert_not_called()
        catalog = first.catalog
        assert first.catalog is catalog
        assert configure.call_count == 1
        assert second.catalog is not catalog
        assert second.catalog.cache_directory != catalog.cache_directory
        assert first.catalog is catalog
        assert configure.call_count == 2


def test_publish_and_workspace_helpers_share_catalog_and_allow_a_cold_reader(
    conan_server: LocalServer, tmp_path: Path
) -> None:
    env = Sandbox(conan_server, tmp_path)
    with patch.object(conan_server, "configure", wraps=conan_server.configure) as configure:
        util = env.publish(env.package(env.ref("util"), files={"report": "result"}))
        published = env.publish_set(
            {"cpu": env.package(env.ref("cpu"))}, dependencies={"cpu": (util.ref,)}
        )
        workspace = env.workspace()
        assert workspace.path.is_file()
        graph = Graph(
            env.catalog, env.package(env.ref("top")), dependencies=(published["cpu"].ref,)
        )
        top = env.publish(graph)
        assert env.catalog.info(top.ref).ref == top.ref
        assert configure.call_count == 1
        reader = env.new_catalog()
        assert reader is not env.catalog
        assert reader.cache_directory != env.catalog.cache_directory
        assert reader.get(util.ref).files["report"].read_text() == "result"
        assert env.new_catalog() is not reader
        assert configure.call_count == 3
        assert len(list(env.root.glob("client-*"))) == 3
        assert str(top.ref) in env.diagnostics()
