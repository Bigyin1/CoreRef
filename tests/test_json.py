"""JSON transport and freezing preserve consumer values without domain rules."""

from __future__ import annotations

import json
from collections.abc import Mapping
from math import inf, nan

import pytest
from frozendict import deepfreeze

from coreref.api.records import JSON, Record
from tests.helpers import local_record, release, run


@pytest.mark.parametrize("value", [nan, inf, -inf])
def test_nonfinite_numbers_cannot_be_written_as_json(value: float) -> None:
    with pytest.raises(ValueError):
        Record(release("nonfinite").ref, {"value": value})


def test_large_integers_are_consumer_data() -> None:
    record = Record(release("large").ref, {"number": 2**100})
    assert json.loads(json.dumps(record.to_dict(), allow_nan=False))["payload"] == {
        "number": 2**100
    }


def test_deepfreeze_detaches_nested_containers() -> None:
    nested: dict[str, JSON] = {"ports": [1, 1.0, -0.0, True, None]}
    original: dict[str, JSON] = {"nested": [nested]}
    frozen: Mapping[str, JSON] = deepfreeze(original)
    nested["ports"] = []
    original.clear()
    array = frozen["nested"]
    assert isinstance(array, tuple)
    item = array[0]
    assert isinstance(item, Mapping)
    assert item["ports"] == (1, 1.0, -0.0, True, None)
    encoded = local_record(Record(release("frozen").ref, frozen)).to_json()
    assert '"payload":{"nested":[{"ports":[1,1.0,-0.0,true,null]}]}' in encoded
    with pytest.raises(TypeError):
        item["ports"] = ()  # type: ignore[index]


def test_consumer_timestamps_are_not_interpreted() -> None:
    record = Record(run("timestamps").ref, {"started_at": "any user format"})
    assert json.loads(json.dumps(record.to_dict(), allow_nan=False))["payload"] == record.payload
