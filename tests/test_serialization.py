"""The stored envelope is typed; resources and metadata are opaque JSON."""

from __future__ import annotations

import json

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import InvalidPackage
from coreref.api.records import JSON, Record
from tests.helpers import content, local_record, release, run


def test_registry_roundtrip_preserves_content_and_nested_immutability(*, catalog: Catalog) -> None:
    resources: tuple[JSON, ...] = (
        {"provider": "custom", "version": False, "nested": [{"ports": [1, 2]}]},
        "user-owned reference",
        None,
    )
    a = Record(
        release("source", catalog=catalog).ref,
        payload={
            "resources": resources,
            "numbers": [1, 1.0, -0.0, False, True, 2**100],
            "unicode": "Содержимое 💡",
        },
    )
    execution = Record(run("run", catalog=catalog).ref, resources)
    original = tuple(catalog.publish(local_record(record)) for record in (a, execution))
    records = tuple(record.ref.info() for record in original)
    assert records == original
    assert tuple(json.dumps(record.payload, allow_nan=False) for record in records) == tuple(
        json.dumps(record.payload, allow_nan=False) for record in original
    )
    with pytest.raises(TypeError):
        content(records[0])["new"] = True  # type: ignore[index]


@pytest.mark.parametrize("payload", ["{", "null", "[]", "{}", '[["metadata", {}]]'])
def test_invalid_envelopes(payload: str, *, catalog: Catalog) -> None:
    record = catalog.publish(local_record(release("broken", catalog=catalog)))
    directory = catalog._client.cache_path(str(record.ref))
    (directory / "record.json").write_text(payload)
    with pytest.raises(InvalidPackage):
        record.ref.info()
