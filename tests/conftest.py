"""In-memory instances provide the isolated dependency API contract."""

from collections.abc import Callable, Iterator

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.protocols import Backend
from hwcatalog.backends.memory import MemoryBackend

type Factory = Callable[[], Backend]


@pytest.fixture
def backend_factory() -> Iterator[Factory]:
    clients: list[Backend] = []

    def create() -> Backend:
        backend = MemoryBackend()
        clients.append(backend)
        return backend

    yield create
    for client in clients:
        client.close()


@pytest.fixture
def catalog(backend_factory: Factory) -> Catalog:
    return Catalog(backend_factory())
