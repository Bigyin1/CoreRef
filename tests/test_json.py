"""Verify the JSON metadata stored in locks and release indexes."""

import json
from collections.abc import Mapping
from math import inf, nan
from pathlib import Path

import pytest
from pydantic import JsonValue, ValidationError

from coreref.api.packages import Ref
from coreref.api.packages.catalog import _Source

_SOURCE = Path("/nix/store/" + "a" * 32 + "-source")


@pytest.mark.parametrize("value", [nan, inf, -inf])
def test_nonfinite_numbers_cannot_be_written_as_json(value: float) -> None:
    with pytest.raises(ValidationError):
        _Source(source=_SOURCE, payload={"value": value})


def test_json_round_trip_preserves_consumer_values() -> None:
    data: dict[str, JsonValue] = {"number": 2**100, "started_at": "any user format"}
    metadata = _Source(source=_SOURCE, payload=data)
    assert json.loads(metadata.model_dump_json())["payload"] == data


def test_record_detaches_nested_metadata_and_outputs() -> None:
    output = _SOURCE / "rtl.v"
    metadata = _Source(
        source=_SOURCE,
        payload={"nested": [{"ports": [1, 1.0, -0.0, True, None]}]},
        outputs={"rtl.v": output},
    )
    record = metadata.record(Ref("demo.frozen/1.0"))
    assert isinstance(metadata.payload, dict)
    metadata.payload.clear()
    metadata.outputs.clear()
    assert record.outputs == {"rtl.v": output}
    assert isinstance(record.payload, Mapping)
    nested = record.payload["nested"]
    assert isinstance(nested, tuple)
    (item,) = nested
    assert isinstance(item, Mapping)
    assert item["ports"] == (1, 1.0, -0.0, True, None)
    with pytest.raises(TypeError):
        item["ports"] = ()  # type: ignore[index]
