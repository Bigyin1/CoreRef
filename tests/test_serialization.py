"""The stored envelope is typed; resources and metadata are opaque JSON."""

import json
from dataclasses import replace

import pytest

from hwcatalog._internal.validation import normalize_record
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.records import Record
from hwcatalog.api.types import JSON
from tests.helpers import content, release, run


def test_roundtrip_preserves_content_and_nested_immutability(*, catalog: Catalog) -> None:
    resources: tuple[JSON, ...] = (
        {"provider": "custom", "version": False, "nested": [{"ports": [1, 2]}]},
        "user-owned reference",
        None,
    )
    a = replace(
        release("source", catalog=catalog),
        payload={
            "resources": resources,
            "numbers": [1, 1.0, -0.0, False, True, 2**100],
            "unicode": "Содержимое 💡",
        },
    )
    execution = replace(run("run", catalog=catalog), payload=resources)
    original = tuple(normalize_record(record) for record in (a, execution))
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
