"""JSON transport and freezing preserve consumer values without domain rules."""

from collections.abc import Mapping
from dataclasses import replace
from math import inf, nan

import pytest
from frozendict import deepfreeze

from hwcatalog.api.types import JSON
from tests.helpers import release, run


@pytest.mark.parametrize("value", [nan, inf, -inf])
def test_nonfinite_numbers_cannot_be_written_as_json(value: float) -> None:
    record = replace(release("nonfinite"), payload={"value": value})
    with pytest.raises(ValueError):
        record.to_json()


def test_large_integers_are_consumer_data() -> None:
    record = replace(release("large"), payload={"number": 2**100})
    assert type(record).from_json(record.to_json()) == record


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
    encoded = replace(release("frozen"), payload=frozen).to_json()
    assert '"payload":{"nested":[{"ports":[1,1.0,-0.0,true,null]}]}' in encoded
    with pytest.raises(TypeError):
        item["ports"] = ()  # type: ignore[index]


def test_consumer_timestamps_are_not_interpreted() -> None:
    record = replace(run("timestamps"), payload={"started_at": "any user format"})
    assert type(record).from_json(record.to_json()).payload == record.payload
