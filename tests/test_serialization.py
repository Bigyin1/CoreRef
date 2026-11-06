"""The stored envelope is typed; resources and metadata are opaque JSON."""

import json

import pytest

from hwcatalog.api.catalog import Catalog
from hwcatalog.api.records import JSON, Record
from tests.helpers import content, release, run


def test_roundtrip_preserves_content_and_nested_immutability(*, catalog: Catalog) -> None:
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
    original = tuple(record for record in (a, execution))
    records = tuple(
        type(record).from_json(json.dumps(record.to_dict(), allow_nan=False), catalog=catalog)
        for record in original
    )
    assert records == original
    assert tuple(json.dumps(record.to_dict(), allow_nan=False) for record in records) == tuple(
        json.dumps(record.to_dict(), allow_nan=False) for record in original
    )
    with pytest.raises(TypeError):
        content(records[0])["new"] = True  # type: ignore[index]


@pytest.mark.parametrize("payload", ["{", "null", "[]", "{}", '[["metadata", {}]]'])
def test_invalid_envelopes(payload: str, *, catalog: Catalog) -> None:
    with pytest.raises((ValueError, TypeError, KeyError)):
        Record.from_json(payload, catalog=catalog)
