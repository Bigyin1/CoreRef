"""Opt-in pytest fixtures: pytest_plugins = ['coreref.testing.pytest_plugin']."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from ..api.catalog import Catalog
from ..tools.deps import DepsFile
from .sandbox import Sandbox, environment
from .server import LocalServer


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "coreref: uses an isolated CoreRef package environment")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if isinstance(item, pytest.Function) and "coreref_environment" in item.fixturenames:
            item.add_marker("coreref")


@pytest.fixture
def coreref_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Sandbox]:
    """Own the server and fixture directories for one test."""
    monkeypatch.delenv("CONAN_DEFAULT_PROFILE", raising=False)
    monkeypatch.delenv("CONAN_DEFAULT_BUILD_PROFILE", raising=False)
    with environment(tmp_path / "coreref") as sandbox:
        yield sandbox


@pytest.fixture
def coreref_server(coreref_environment: Sandbox) -> LocalServer:
    return coreref_environment.server


@pytest.fixture
def coreref_catalog(coreref_environment: Sandbox) -> Catalog:
    return coreref_environment.new_catalog()


@pytest.fixture
def coreref_project(
    coreref_environment: Sandbox,
    coreref_catalog: Catalog,
    tmp_path: Path,
) -> DepsFile:
    return coreref_environment.project(
        coreref_catalog,
        coreref_environment.ref("consumer"),
        path=tmp_path / "deps.yml",
    )
