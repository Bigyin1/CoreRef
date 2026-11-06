"""The stored envelope is typed; resources and metadata are opaque JSON."""

from dataclasses import replace

import pytest

from hwcatalog._internal.validation import normalize_record
from hwcatalog.api.records import Record
from hwcatalog.api.types import JSON
from tests.helpers import content, release, run


def test_roundtrip_preserves_content_and_nested_immutability() -> None:
    resources: tuple[JSON, ...] = (
        {"provider": "custom", "version": False, "nested": [{"ports": [1, 2]}]},
        "user-owned reference",
        None,
    )
    a = replace(
        release("source"),
        payload={
            "resources": resources,
            "numbers": [1, 1.0, -0.0, False, True, 2**100],
            "unicode": "Содержимое 💡",
        },
    )
    execution = replace(run("run"), payload=resources)
    original = tuple(normalize_record(record) for record in (a, execution))
    records = tuple(type(record).from_json(record.to_json()) for record in original)
    assert records == original
    assert tuple(record.to_json() for record in records) == tuple(
        record.to_json() for record in original
    )
    with pytest.raises(TypeError):
        content(records[0])["new"] = True  # type: ignore[index]


@pytest.mark.parametrize("payload", ["{", "null", "[]", "{}", '[["metadata", {}]]'])
def test_invalid_envelopes(payload: str) -> None:
    with pytest.raises((ValueError, TypeError, KeyError)):
        Record.from_json(payload)
