"""Opt-in pytest fixtures: pytest_plugins = ['coreref.testing.pytest_plugin']."""

from collections.abc import Generator, Iterator
from pathlib import Path
from typing import TypedDict

import pytest

from ..api.packages.catalog import Catalog
from ..api.projects.manifest import ManifestFile
from .sandbox import Sandbox, environment
from .server import LocalServer

_ENVIRONMENT = pytest.StashKey[Sandbox]()


class EnvironmentOptions(TypedDict, total=False):
    """Optional environment settings returned by the coreref_options fixture."""

    projects: Path | None
    repositories: tuple[str, ...]
    namespace: str
    timeout: float


@pytest.fixture
def coreref_options() -> EnvironmentOptions:
    """Override this fixture in conftest.py to configure the test environment."""
    return {}


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item,
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    sandbox = item.stash.get(_ENVIRONMENT, None)
    if report.failed and sandbox is not None:
        report.sections.append(("CoreRef environment", sandbox.diagnostics()))
    return report


@pytest.fixture
def coreref_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    coreref_options: EnvironmentOptions,
) -> Iterator[Sandbox]:
    """Own the server and fixture directories for one test."""
    monkeypatch.delenv("CONAN_DEFAULT_PROFILE", raising=False)
    monkeypatch.delenv("CONAN_DEFAULT_BUILD_PROFILE", raising=False)
    options = coreref_options.copy()
    projects = options.pop("projects", None)
    repositories = options.pop("repositories", ())
    with environment(tmp_path / "coreref", **options) as sandbox:
        request.node.stash[_ENVIRONMENT] = sandbox
        # Load projects after stashing the environment so setup errors include its paths.
        if projects is not None:
            sandbox.load_projects(request.config.rootpath / projects, repositories=repositories)
        elif repositories:
            raise ValueError("coreref repositories require projects")
        yield sandbox


@pytest.fixture
def coreref_server(coreref_environment: Sandbox) -> LocalServer:
    return coreref_environment.server


@pytest.fixture
def coreref_catalog(coreref_environment: Sandbox) -> Catalog:
    return coreref_environment.catalog


@pytest.fixture
def coreref_project(
    coreref_environment: Sandbox,
    tmp_path: Path,
) -> ManifestFile:
    return coreref_environment.project(
        coreref_environment.ref("consumer"),
        path=tmp_path / "manifest.yml",
    )
