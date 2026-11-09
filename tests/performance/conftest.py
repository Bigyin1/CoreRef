"""A private registry shared only by the opt-in benchmark suite."""

from __future__ import annotations

from collections.abc import Iterator
from importlib.metadata import version

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from examples.local_server import local_server
from tests.performance.support import Registry


@pytest.fixture(scope="session")
def registry(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Registry]:
    root = tmp_path_factory.mktemp("performance")
    with local_server(root / "server") as server:
        yield Registry(server, root)


@pytest.fixture(autouse=True)
def report_environment(request: pytest.FixtureRequest) -> None:
    if "benchmark" in request.fixturenames:
        benchmark: BenchmarkFixture = request.getfixturevalue("benchmark")
        benchmark.extra_info.update(
            {name: version(name) for name in ("conan", "coreref", "networkx")}
        )
