"""One reference and record, with opaque consumer-defined content."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

import pytest

from coreref.api.catalog import Catalog
from coreref.api.errors import InvalidPackage
from coreref.api.query import SearchQuery
from coreref.api.records import JSON, JSONSerializable, LocalRecord, Record, Ref
from tests.helpers import local_record


@dataclass
class UserPayload:
    data: JSON
    calls: int = 0

    def to_json(self) -> str:
        self.calls += 1
        return json.dumps(self.data)


def test_minimal_record() -> None:
    record = Record(Ref.from_fields("demo", "core", "release", "default", "1.0"))
    assert record.payload is None
    assert json.loads(json.dumps(record.to_dict(), allow_nan=False))["ref"] == record.ref.to_dict()
    with pytest.raises(AttributeError):
        record.payload = 1  # type: ignore[misc]


@pytest.mark.parametrize("value", [None, True, 12, "custom", [1, {"size": -10}], {"schema": False}])
def test_any_json_payload_roundtrips(catalog: Catalog, value: JSON) -> None:
    record = Record(
        Ref.from_fields("demo", "opaque", "release", "default", "1.0"),
        UserPayload(value),
    )
    record = catalog.publish(local_record(record))
    stored = catalog.get(Ref.from_fields(**record.ref.to_dict()))
    assert stored == record
    assert not isinstance(stored.payload, JSONSerializable)


def test_custom_payload_is_serialized_once_and_frozen(catalog: Catalog) -> None:
    nested: list[JSON] = [1, {"ports": [2, 3]}]
    payload = UserPayload({"nested": nested})
    catalog.publish(
        LocalRecord(Ref.from_fields("demo", "core", "release", "default", "1.0"), payload)
    )
    assert payload.calls == 1
    nested.append(4)
    stored = catalog.get(Ref.from_fields("demo", "core", "release", "default", "1.0"))
    assert isinstance(stored.payload, Mapping)
    assert stored.payload["nested"] == (1, {"ports": (2, 3)})
    with pytest.raises(TypeError):
        stored.payload["new"] = 1  # type: ignore[index]
    assert json.loads(json.dumps(stored.to_dict(), allow_nan=False))["payload"] == {
        "nested": [1, {"ports": [2, 3]}]
    }


def test_invalid_custom_payload_does_not_publish_any_records(catalog: Catalog) -> None:
    class InvalidPayload:
        def to_json(self) -> str:
            return "{"

    with pytest.raises(json.JSONDecodeError):
        catalog.publish(
            local_record(
                Record(
                    Ref.from_fields("demo", "invalid", "release", "default", "1.0"),
                    InvalidPayload(),
                )
            )
        )
    assert not catalog.search(SearchQuery())


def test_namespace_is_part_of_identity(catalog: Catalog) -> None:
    ref = Ref.from_fields("rtl-ip", "core", "release", "default", "1.0")
    same = Ref.from_fields("rtl-ip", "core", "release", "default", "1.0")
    other = Ref.from_fields(**(ref.to_dict() | {"namespace": "connection-ip"}))
    assert ref == same
    assert hash(ref) == hash(same)
    assert ref.package_name == Ref.from_fields(**(ref.to_dict() | {"version": "2.0"})).package_name
    assert ref != other
    assert ref.package_name != other.package_name
    catalog.publish(local_record(Record(ref)))
    catalog.publish(local_record(Record(other)))
    stored = catalog.get(Ref.from_fields(**same.to_dict()))
    assert stored.ref.without_revision() == ref
    assert catalog.publish(local_record(Record(same))) == stored


@pytest.mark.parametrize(
    "coordinates",
    [
        ("demo", "", "release", "default", "1.0"),
        ("demo", "core", "release", "default", " "),
        ("demo", "core", "", "default", "1.0"),
        ("demo", "core", " ", "default", "1.0"),
        ("", "core", "release", "default", "1.0"),
        (" ", "core", "release", "default", "1.0"),
    ],
)
def test_reference_construction_rejects_noncanonical_fields(
    coordinates: tuple[str, str, str, str, str],
) -> None:
    with pytest.raises(InvalidPackage):
        Ref.from_fields(*coordinates)


class CustomKind(str, Enum):
    REPORT = "vendor-report"

    def __str__(self) -> str:
        return str.__str__(self)


@pytest.mark.parametrize("kind", ["artifact", "execution", CustomKind.REPORT, "custom"])
def test_consumer_kinds(catalog: Catalog, kind: str) -> None:
    ref = Ref.from_fields("demo", "core", kind, "default", "1.0")
    catalog.publish(local_record(Record(ref)))
    stored = catalog.get(Ref.from_fields(**ref.to_dict()))
    assert stored.ref.view == kind
    assert type(stored.ref.view) is str


@pytest.mark.parametrize("field", ["namespace", "component", "view", "type", "version"])
def test_coordinate_fields_are_required(field: str) -> None:
    coordinates = {
        "namespace": "demo",
        "component": "core",
        "view": "rtl",
        "type": "default",
        "version": "1.0",
    }
    coordinates.pop(field)
    with pytest.raises(TypeError):
        Ref.from_fields(**coordinates)
