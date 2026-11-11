"""Compare coordinate-only and metadata search over bounded namespaces."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from functools import partial
from operator import methodcaller

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from coreref.api.query import SearchQuery
from coreref.api.records import Record, Ref
from coreref.testing import local_server
from tests.performance.support import Dataset, Registry

pytestmark = [pytest.mark.performance, pytest.mark.conan_remote]
_CALL = methodcaller("__call__")


@pytest.fixture(scope="module", params=[10, 100])
def search_registry(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Registry, Dataset]]:
    root = tmp_path_factory.mktemp(f"search-{request.param}")
    with local_server(root / "server") as server:
        registry = Registry(server, root)
        yield registry, registry.dataset("wide", request.param)


def search_setup(
    registry: Registry,
    query: SearchQuery,
    *,
    metadata: bool,
) -> Callable[[], tuple[tuple[Callable[[], object]], dict[str, object]]]:
    def setup() -> tuple[tuple[Callable[[], object]], dict[str, object]]:
        reader = registry.reader()
        operation = reader.search if metadata else reader.search_refs
        return (partial(operation, query),), {}

    return setup


@pytest.mark.parametrize("narrow", [False, True], ids=["all", "component"])
@pytest.mark.parametrize("metadata", [False, True], ids=["refs", "records"])
def test_search(
    benchmark: BenchmarkFixture,
    search_registry: tuple[Registry, Dataset],
    *,
    narrow: bool,
    metadata: bool,
) -> None:
    registry, dataset = search_registry
    size = len(dataset.records)
    query = SearchQuery(
        namespace=dataset.namespace, component="node000" if narrow else None, view="rtl"
    )
    benchmark.extra_info.update(
        packages=size, matches=1 if narrow else size, metadata=metadata, cache="cold"
    )
    records = benchmark.pedantic(
        _CALL,
        setup=search_setup(registry, query, metadata=metadata),
        rounds=3,
    )
    expected = dataset.records[:1] if narrow else dataset.records
    assert {r.ref if isinstance(r, Record) else r for r in records} == {r.ref for r in expected}
    assert all(isinstance(r, Record if metadata else Ref) for r in records)
