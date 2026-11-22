"""Resolved project graphs expose dependency-first traversals."""

from __future__ import annotations

import pytest

from coreref.api.errors import InvalidPackage
from coreref.api.packages.catalog import Catalog
from coreref.api.projects.graph import Graph
from tests.helpers import local_record, release


def test_ordering_includes_shared_dependencies_once(*, catalog: Catalog) -> None:
    common, cpu, gpio, private, top = (
        release(name) for name in ("common", "cpu", "gpio", "private", "top")
    )
    for record in (common, private):
        catalog.publish(local_record(record))
    cpu = catalog.publish(local_record(cpu), dependencies=(common.ref, private.ref))
    gpio = catalog.publish(local_record(gpio), dependencies=(common.ref,))
    common, cpu, gpio, private = (
        catalog.get(record.ref) for record in (common, cpu, gpio, private)
    )
    snapshot = Graph(catalog, local_record(top), dependencies=(cpu.ref, gpio.ref))
    order = tuple(record.ref for record in snapshot.dependencies(transitive=True))
    assert set(order) == {common.ref, private.ref, cpu.ref, gpio.ref}
    assert order.index(common.ref) < order.index(cpu.ref)
    assert order.index(common.ref) < order.index(gpio.ref)
    assert order.index(private.ref) < order.index(cpu.ref)


def test_ordering_rejects_unknown_roots(*, catalog: Catalog) -> None:
    root, middle, leaf = (release(name) for name in ("root", "middle", "leaf"))
    leaf = catalog.publish(local_record(leaf))
    middle = catalog.publish(local_record(middle), dependencies=(leaf.ref,))
    snapshot = Graph(catalog, local_record(root), dependencies=(middle.ref,))
    absent = release("absent").ref
    with pytest.raises(InvalidPackage, match="Unknown"):
        snapshot.dependencies(absent, transitive=True)
