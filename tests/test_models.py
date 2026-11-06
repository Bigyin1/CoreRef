"""One reference and record, with opaque consumer-defined content."""

import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError, dataclass, replace
from enum import StrEnum

import pytest

from hwcatalog._internal.validation import normalize_record
from hwcatalog.api.catalog import Catalog
from hwcatalog.api.errors import Conflict, InvalidPackage
from hwcatalog.api.protocols import JSONSerializable
from hwcatalog.api.records import Record, Ref
from hwcatalog.api.types import JSON


@dataclass
class UserPayload:
    data: JSON
    calls: int = 0

    def to_json(self) -> str:
        self.calls += 1
        return json.dumps(self.data)


def test_minimal_record() -> None:
    record = Record(Ref("demo", "core", "release", "default", "1.0"))
    assert record.payload is None
    assert Record.from_json(record.to_json()) == record
    with pytest.raises(FrozenInstanceError):
        record.payload = 1  # type: ignore[misc]


@pytest.mark.parametrize("value", [None, True, 12, "custom", [1, {"size": -10}], {"schema": False}])
def test_any_json_payload_roundtrips(catalog: Catalog, value: JSON) -> None:
    record = Record(Ref("demo", "opaque", "release", "default", "1.0"), UserPayload(value))
    catalog.publish(record)
    stored = Ref(**record.ref.to_dict(), catalog=catalog).get()
    assert stored == Record.from_json(record.to_json())
    assert not isinstance(stored.payload, JSONSerializable)


def test_custom_payload_is_serialized_once_and_frozen(catalog: Catalog) -> None:
    nested: list[JSON] = [1, {"ports": [2, 3]}]
    payload = UserPayload({"nested": nested})
    catalog.publish(Record(Ref("demo", "core", "release", "default", "1.0"), payload))
    assert payload.calls == 1
    nested.append(4)
    stored = Ref("demo", "core", "release", "default", "1.0", catalog=catalog).get()
    assert isinstance(stored.payload, Mapping)
    assert stored.payload["nested"] == (1, {"ports": (2, 3)})
    with pytest.raises(TypeError):
        stored.payload["new"] = 1  # type: ignore[index]
    assert Record.from_json(stored.to_json()) == stored


def test_invalid_custom_payload_does_not_publish_any_records(catalog: Catalog) -> None:
    class InvalidPayload:
        def to_json(self) -> str:
            return "{"

    with pytest.raises(json.JSONDecodeError):
        catalog.publish(
            Record(Ref("demo", "invalid", "release", "default", "1.0"), InvalidPayload())
        )
    assert not catalog.search()


def test_namespace_is_part_of_identity(catalog: Catalog) -> None:
    ref = Ref("rtl-ip", "core", "release", "default", "1.0")
    same = Ref("rtl-ip", "core", "release", "default", "1.0")
    other = replace(ref, namespace="connection-ip")
    assert ref == same
    assert hash(ref) == hash(same)
    assert ref.package_name == replace(ref, version="2.0").package_name
    assert ref != other
    assert ref.package_name != other.package_name
    catalog.publish(Record(ref))
    catalog.publish(Record(other))
    assert Ref(**same.to_dict(), catalog=catalog).get().ref == ref
    with pytest.raises(Conflict):
        catalog.publish(Record(same))


@pytest.mark.parametrize(
    "ref",
    [
        Ref("demo", "", "release", "default", "1.0"),
        Ref("demo", "core", "release", "default", " "),
        Ref("demo", "core", "", "default", "1.0"),
        Ref("demo", "core", " ", "default", "1.0"),
        Ref("", "core", "release", "default", "1.0"),
        Ref(" ", "core", "release", "default", "1.0"),
    ],
)
def test_required_reference_fields(ref: Ref) -> None:
    with pytest.raises(InvalidPackage):
        normalize_record(Record(ref))


class CustomKind(StrEnum):
    REPORT = "vendor-report"


@pytest.mark.parametrize("kind", ["artifact", "execution", CustomKind.REPORT, "custom"])
def test_consumer_kinds(catalog: Catalog, kind: str) -> None:
    ref = Ref("demo", "core", kind, "default", "1.0")
    catalog.publish(Record(ref))
    stored = Ref(**ref.to_dict(), catalog=catalog).get()
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
        Ref(**coordinates, catalog=None)
