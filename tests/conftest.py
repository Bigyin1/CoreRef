"""The same public contract runs on memory or an owned, disposable Compose database."""

import os
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from neo4j import GraphDatabase
from neo4j.exceptions import DriverError, Neo4jError

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.protocols import Backend
from hwcatalog.backends.memgraph import MemgraphBackend
from hwcatalog.backends.memory import MemoryBackend

ROOT = Path(__file__).resolve().parents[1]


class Compose:
    def __init__(self) -> None:
        self.project = "hwcatalog-test-" + uuid4().hex[:12]
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.uri = f"bolt://localhost:{self.port}"

    def run(self, *args: str) -> None:
        subprocess.run(  # noqa: S603 - fixed Compose invocation for our disposable service
            [
                shutil.which("docker") or "/usr/local/bin/docker",
                "compose",
                "-f",
                str(ROOT / "compose.yaml"),
                "-p",
                self.project,
                *args,
            ],
            env={**os.environ, "HWCATALOG_BOLT_PORT": str(self.port)},
            check=True,
            timeout=180,
            capture_output=True,
        )

    def ready(self) -> None:
        deadline = time.monotonic() + 90
        with GraphDatabase.driver(self.uri, connection_timeout=1) as driver:
            while True:
                try:
                    driver.verify_connectivity()
                    return
                except DriverError, Neo4jError, OSError:
                    if time.monotonic() >= deadline:
                        raise
                    threading.Event().wait(0.2)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--backend", choices=("memory", "memgraph"), default="memory")


@pytest.fixture(scope="session")
def compose(request: pytest.FixtureRequest) -> Iterator[Compose]:
    if request.config.getoption("backend") != "memgraph":
        pytest.skip("Explicit integration test; run with --backend=memgraph")
    service = Compose()
    try:
        service.run("up", "-d")
        service.ready()
        yield service
    finally:
        service.run("down", "-v")


type Factory = Callable[[], Backend]


@pytest.fixture
def backend_factory(request: pytest.FixtureRequest) -> Iterator[Factory]:
    clients: list[Backend] = []
    uri: str | None = None
    if request.config.getoption("backend") == "memgraph":
        service = request.getfixturevalue("compose")
        uri = service.uri
        # Only this fixture's random Compose project/volume is ever cleared.
        with GraphDatabase.driver(uri) as driver, driver.session() as session:
            session.run("MATCH (n:HCRecord) DETACH DELETE n").consume()

    def create() -> Backend:
        if uri:
            client: Backend = MemgraphBackend(uri)
        else:
            client = MemoryBackend()
        clients.append(client)
        return client

    yield create
    for client in clients:
        client.close()


@pytest.fixture
def catalog(backend_factory: Factory) -> Catalog:
    return Catalog(backend_factory())


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("backend") == "memory":
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="Use --backend=memgraph for integration"))
