"""Opt-in pytest fixtures: pytest_plugins = ['coreref.testing.pytest_plugin']."""

from collections.abc import Generator, Iterator
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..api.catalog import Catalog
from ..tools.deps import DepsFile
from .sandbox import Sandbox, environment
from .server import LocalServer

_ENVIRONMENT = pytest.StashKey[Sandbox]()


class _Options(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    projects: str | Path | None = None
    repositories: tuple[str, ...] = ()
    namespace: str = "test"
    timeout: float = Field(default=5, gt=0, allow_inf_nan=False)


def _options(request: pytest.FixtureRequest) -> _Options:
    marker = request.node.get_closest_marker("coreref")
    if marker is not None and marker.args:
        raise pytest.UsageError("coreref marker accepts keyword arguments only")
    try:
        return _Options.model_validate(marker.kwargs if marker is not None else {})
    except ValidationError as error:
        raise pytest.UsageError(f"Invalid coreref marker: {error}") from error


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item,
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    sandbox = item.stash.get(_ENVIRONMENT, None)
    if report.failed and sandbox is not None:
        report.sections.append(("CoreRef environment", sandbox.diagnostics()))
    return report


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "coreref: uses an isolated CoreRef package environment")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if (
            isinstance(item, pytest.Function)
            and "coreref_environment" in item.fixturenames
            and item.get_closest_marker("coreref") is None
        ):
            item.add_marker("coreref")


@pytest.fixture
def coreref_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[Sandbox]:
    """Own the server and fixture directories for one test."""
    monkeypatch.delenv("CONAN_DEFAULT_PROFILE", raising=False)
    monkeypatch.delenv("CONAN_DEFAULT_BUILD_PROFILE", raising=False)
    options = _options(request)
    with environment(
        tmp_path / "coreref",
        namespace=options.namespace,
        timeout=options.timeout,
    ) as sandbox:
        request.node.stash[_ENVIRONMENT] = sandbox
        if options.projects is not None:
            source = request.node.path.parent / options.projects
            sandbox.load_projects(source, repositories=options.repositories)
        elif options.repositories:
            raise pytest.UsageError("coreref repositories require projects")
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
